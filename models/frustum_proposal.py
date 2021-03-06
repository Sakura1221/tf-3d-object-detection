import numpy as np

'''
Note from KITTI Object detection note:

The coordinates in the camera coordinate system can be projected in the image
by using the 3x4 projection matrix in the calib folder, where for the left
color camera for which the images are provided, P2 must be used. The
difference between rotation_y and alpha is, that rotation_y is directly
given in camera coordinates, while alpha also considers the vector from the
camera center to the object center, to compute the relative orientation of
the object with respect to the camera. For example, a car which is facing
along the X-axis of the camera coordinate system corresponds to rotation_y=0,
no matter where it is located in the X/Z plane (bird's eye view), while
alpha is zero only, when this object is located along the Z-axis of the
camera. When moving the car away from the Z-axis, the observation angle
will change.

To project a point from Velodyne coordinates into the left color image,
you can use this formula: x = P2 * R0_rect * Tr_velo_to_cam * y
For the right color image: x = P3 * R0_rect * Tr_velo_to_cam * y

Note: All matrices are stored row-major, i.e., the first values correspond
to the first row. R0_rect contains a 3x3 matrix which you need to extend to
a 4x4 matrix by adding a 1 as the bottom-right element and 0's elsewhere.
Tr_xxx is a 3x4 matrix (R|t), which you need to extend to a 4x4 matrix 
in the same way!

Note, that while all this information is available for the training data,
only the data which is actually needed for the particular benchmark must
be provided to the evaluation server. However, all 15 values must be provided
at all times, with the unused ones set to their default values (=invalid) as
specified in writeLabels.m. Additionally a 16'th value must be provided
with a floating value of the score for a particular detection, where higher
indicates higher confidence in the detection. The range of your scores will
be automatically determined by our evaluation server, you don't have to
normalize it, but it should be roughly linear. If you use writeLabels.m for
writing your results, this function will take care of storing all required
data correctly.

'''


class FrustumProposal(object):
    def __init__(self, calibs):
        assert 'P' and 'Tr_velo_to_cam' and 'R0_rect' in calibs

        self.P = calibs['P']
        self.P = np.reshape(self.P, [3, 4])

        self.V2C = calibs['Tr_velo_to_cam']
        self.V2C = np.reshape(self.V2C, [3, 4])

        self.C2V = self.inverse_rigid_trans(self.V2C)

        self.R0 = calibs['R0_rect']
        self.R0 = np.reshape(self.R0, [3, 3])

    @staticmethod
    def inverse_rigid_trans(Tr):
        ''' Inverse a rigid body transform matrix (3x4 as [R|t])
			[R'|-R't; 0|1]
		'''
        inv_Tr = np.zeros_like(Tr)  # 3x4
        inv_Tr[0:3, 0:3] = np.transpose(Tr[0:3, 0:3])
        inv_Tr[0:3, 3] = np.dot(-np.transpose(Tr[0:3, 0:3]), Tr[0:3, 3])
        return inv_Tr

    def _cart2hom(self, pts_3d):
        ''' Input: nx3 points in Cartesian
			Oupput: nx4 points in Homogeneous by pending 1
		'''
        n = pts_3d.shape[0]
        pts_3d_hom = np.hstack((pts_3d, np.ones((n, 1))))
        return pts_3d_hom

    def _project_velo_to_ref(self, pts_3d_velo):
        pts_3d_velo = self._cart2hom(pts_3d_velo)  # nx4
        return np.dot(pts_3d_velo, np.transpose(self.V2C))

    def _project_ref_to_velo(self, pts_3d_ref):
        pts_3d_ref = self._cart2hom(pts_3d_ref)  # nx4
        return np.dot(pts_3d_ref, np.transpose(self.C2V))

    def _project_rect_to_ref(self, pts_3d_rect):
        ''' Input and Output are nx3 points '''
        return np.transpose(np.dot(np.linalg.inv(self.R0), np.transpose(pts_3d_rect)))

    def _project_ref_to_rect(self, pts_3d_ref):
        ''' Input and Output are nx3 points '''
        return np.transpose(np.dot(self.R0, np.transpose(pts_3d_ref)))

    def project_rect_to_velo(self, pts_3d_rect):
        ''' Input: nx3 points in rect camera coord.
			Output: nx3 points in velodyne coord.
		'''
        pts_3d_ref = self._project_rect_to_ref(pts_3d_rect)
        return self._project_ref_to_velo(pts_3d_ref)

    def _project_velo_to_rect(self, pts_3d_velo):
        pts_3d_ref = self._project_velo_to_ref(pts_3d_velo)
        return self._project_ref_to_rect(pts_3d_ref)

    def _project_rect_to_image(self, pts_3d_rect):
        ''' Input: nx3 points in rect camera coord.
			Output: nx2 points in image2 coord.
		'''
        pts_3d_rect = self._cart2hom(pts_3d_rect)
        pts_2d = np.dot(pts_3d_rect, np.transpose(self.P))  # nx3
        pts_2d[:, 0] /= pts_2d[:, 2]
        pts_2d[:, 1] /= pts_2d[:, 2]
        return pts_2d[:, 0:2]

    def _project_velo_to_image(self, pts_3d_velo):
        ''' Input: nx3 points in velodyne coord.
			Output: nx2 points in image2 coord.
		'''
        pts_3d_rect = self._project_velo_to_rect(pts_3d_velo)
        return self._project_rect_to_image(pts_3d_rect)

    def _get_lidar_in_image_fov(self, pc_velo, xmin, ymin, xmax, ymax,
                                return_more=False, clip_distance=2.0):
        ''' Filter lidar points, keep those in image FOV '''
        pts_2d = self._project_velo_to_image(pc_velo)
        fov_inds = (pts_2d[:, 0] < xmax) & (pts_2d[:, 0] >= xmin) & \
                   (pts_2d[:, 1] < ymax) & (pts_2d[:, 1] >= ymin)
        fov_inds = fov_inds & (pc_velo[:, 0] > clip_distance)
        imgfov_pc_velo = pc_velo[fov_inds, :]
        if return_more:
            return imgfov_pc_velo, pts_2d, fov_inds
        else:
            return imgfov_pc_velo

    def get_frustum_proposal(self, img_shape, boxes2d, pc_velo):
        print('[FrustumProposal] Fetching frustum proposal from:')
        print('[FrustumProposal] image_shape: {} '.format(img_shape))
        print('[FrustumProposal] boxes2d: {} '.format(boxes2d))
        print('[FrustumProposal] pc_velo.shape: {} '.format(pc_velo.shape))
        frustum_proposals = []
        frustum_proposals_velo = []
        img_height, img_width, _ = img_shape
        _num_objs = len(boxes2d)
        _, pc_image_coord, img_fov_inds = self._get_lidar_in_image_fov(pc_velo[:, 0:3], 0, 0, img_width, img_height, True)
        pc_rect = np.zeros_like(pc_velo)
        pc_rect[:, 0:3] = self._project_velo_to_rect(pc_velo[:, 0:3])
        pc_rect[:, 3] = pc_velo[:, 3]
        for obj_idx in range(_num_objs):
            box2d = boxes2d[obj_idx]
            xmin, ymin, xmax, ymax = box2d
            box_fov_inds = (pc_image_coord[:, 0] < xmax) & \
                           (pc_image_coord[:, 0] >= xmin) & \
                           (pc_image_coord[:, 1] < ymax) & \
                           (pc_image_coord[:, 1] >= ymin)
            box_fov_inds = box_fov_inds & img_fov_inds
            pc_in_box_fov = pc_rect[box_fov_inds, :]
            # Below is equivalent to the commented one line code. I do this to verify the projection
            pc_in_velo_fov = np.zeros_like(pc_in_box_fov)
            pc_in_velo_fov[:, 0:3] = self.project_rect_to_velo(pc_in_box_fov[:, 0:3])
            pc_in_velo_fov[:, 3] = pc_in_box_fov[:, 3]

            # pc_in_velo_fov = pc_velo[box_fov_inds, :]
            frustum_proposals.append(pc_in_box_fov)
            frustum_proposals_velo.append(pc_in_velo_fov)
        print('[Frustum Proposal] Propose %s frustum proposals' % len(frustum_proposals))
        return frustum_proposals, frustum_proposals_velo
