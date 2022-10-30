import jax
import jax.numpy as jnp


def transform_from_pos(t):
    return jnp.vstack(
        [jnp.hstack([jnp.eye(3), t.reshape(3,1)]), jnp.array([0.0, 0.0, 0.0, 1.0])]
    )

def transform_from_rot(rot):
    return jnp.vstack(
        [jnp.hstack([rot, jnp.zeros(3,1)]), jnp.array([0.0, 0.0, 0.0, 1.0])]
    )

def transform_from_rot_and_pos(rot, t):
    return jnp.vstack(
        [jnp.hstack([rot, t.reshape(3,1)]), jnp.array([0.0, 0.0, 0.0, 1.0])]
    )

def transform_from_axis_angle(axis, angle):
    sina = jnp.sin(angle)
    cosa = jnp.cos(angle)
    direction = axis / jnp.linalg.norm(axis)
    # rotation matrix around unit vector
    R = jnp.diag(jnp.array([cosa, cosa, cosa]))
    R = R + jnp.outer(direction, direction) * (1.0 - cosa)
    direction = direction * sina
    R = R + jnp.array([[0.0, -direction[2], direction[1]],
                        [direction[2], 0.0, -direction[0]],
                        [-direction[1], direction[0], 0.0]])
    M = jnp.identity(4)
    M = M.at[:3, :3].set(R)
    return M

# move point cloud to a specified pose
# coords: (N,3) point cloud
# pose: (4,4) pose matrix. rotation matrix in top left (3,3) and translation in (:3,3)
def apply_transform(coords, transform):
    coords = jnp.einsum(
        'ij,...j->...i',
        transform,
        jnp.concatenate([coords, jnp.ones(coords.shape[:-1] + (1,))], axis=-1),
    )[..., :-1]
    return coords

def quaternion_to_rotation_matrix(Q):
    """
    Covert a quaternion into a full three-dimensional rotation matrix.
 
    Input
    :param Q: A 4 element array representing the quaternion (q0,q1,q2,q3) 
 
    Output
    :return: A 3x3 element matrix representing the full 3D rotation matrix. 
             This rotation matrix converts a point in the local reference 
             frame to a point in the global reference frame.
    """
    # Extract the values from Q
    q0 = Q[0]
    q1 = Q[1]
    q2 = Q[2]
    q3 = Q[3]
     
    # First row of the rotation matrix
    r00 = 2 * (q0 * q0 + q1 * q1) - 1
    r01 = 2 * (q1 * q2 - q0 * q3)
    r02 = 2 * (q1 * q3 + q0 * q2)
     
    # Second row of the rotation matrix
    r10 = 2 * (q1 * q2 + q0 * q3)
    r11 = 2 * (q0 * q0 + q2 * q2) - 1
    r12 = 2 * (q2 * q3 - q0 * q1)
     
    # Third row of the rotation matrix
    r20 = 2 * (q1 * q3 - q0 * q2)
    r21 = 2 * (q2 * q3 + q0 * q1)
    r22 = 2 * (q0 * q0 + q3 * q3) - 1
     
    # 3x3 rotation matrix
    rot_matrix = jnp.array([[r00, r01, r02],
                           [r10, r11, r12],
                           [r20, r21, r22]])
                            
    return rot_matrix

def rotation_matrix_to_quaternion(matrix):

    def case0(m):
        t = 1 + m[0, 0] - m[1, 1] - m[2, 2]
        q = jnp.array(
            [
                m[2, 1] - m[1, 2],
                t,
                m[1, 0] + m[0, 1],
                m[0, 2] + m[2, 0],
            ]
        )
        return t, q

    def case1(m):
        t = 1 - m[0, 0] + m[1, 1] - m[2, 2]
        q = jnp.array(
            [
                m[0, 2] - m[2, 0],
                m[1, 0] + m[0, 1],
                t,
                m[2, 1] + m[1, 2],
            ]
        )
        return t, q

    def case2(m):
        t = 1 - m[0, 0] - m[1, 1] + m[2, 2]
        q = jnp.array(
            [
                m[1, 0] - m[0, 1],
                m[0, 2] + m[2, 0],
                m[2, 1] + m[1, 2],
                t,
            ]
        )
        return t, q

    def case3(m):
        t = 1 + m[0, 0] + m[1, 1] + m[2, 2]
        q = jnp.array(
            [
                t,
                m[2, 1] - m[1, 2],
                m[0, 2] - m[2, 0],
                m[1, 0] - m[0, 1],
            ]
        )
        return t, q

    # Compute four cases, then pick the most precise one.
    # Probably worth revisiting this!
    case0_t, case0_q = case0(matrix)
    case1_t, case1_q = case1(matrix)
    case2_t, case2_q = case2(matrix)
    case3_t, case3_q = case3(matrix)

    cond0 = matrix[2, 2] < 0
    cond1 = matrix[0, 0] > matrix[1, 1]
    cond2 = matrix[0, 0] < -matrix[1, 1]

    t = jnp.where(
        cond0,
        jnp.where(cond1, case0_t, case1_t),
        jnp.where(cond2, case2_t, case3_t),
    )
    q = jnp.where(
        cond0,
        jnp.where(cond1, case0_q, case1_q),
        jnp.where(cond2, case2_q, case3_q),
    )
    return q * 0.5 / jnp.sqrt(t)