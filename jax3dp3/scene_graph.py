from .transforms_3d import rotation_from_axis_angle, transform_from_rot_and_pos, transform_from_pos, transform_from_axis_angle
import jax.numpy as jnp
import networkx as nx
import jax

def get_contact_planes(dimensions):
    return jnp.stack([
        transform_from_pos(jnp.array([0.0, dimensions[1]/2.0, 0.0])).dot(transform_from_axis_angle(jnp.array([1.0, 0.0, 0.0]), -jnp.pi/2)),
        transform_from_pos(jnp.array([0.0, -dimensions[1]/2.0, 0.0])).dot(transform_from_axis_angle(jnp.array([1.0, 0.0, 0.0]), jnp.pi/2)),
        transform_from_pos(jnp.array([0.0, 0.0, dimensions[2]/2.0])).dot(transform_from_axis_angle(jnp.array([1.0, 0.0, 0.0]), 0.0)),
        transform_from_pos(jnp.array([0.0, 0.0, -dimensions[2]/2.0])).dot(transform_from_axis_angle(jnp.array([1.0, 0.0, 0.0]), jnp.pi)),
        transform_from_pos(jnp.array([-dimensions[0]/2.0, 0.0, 0.0])).dot(transform_from_axis_angle(jnp.array([0.0, 1.0, 0.0]), -jnp.pi/2)),
        transform_from_pos(jnp.array([dimensions[0]/2.0, 0.0, 0.0])).dot(transform_from_axis_angle(jnp.array([0.0, 1.0, 0.0]), jnp.pi/2)),
    ])

def get_contact_transform(contact_params):
    x,y,angle = contact_params
    return (
        transform_from_pos(jnp.array([x,y, 0.0])).dot(
            transform_from_axis_angle(jnp.array([1.0, 1.0, 0.0]), jnp.pi).dot(
                transform_from_axis_angle(jnp.array([0.0, 0.0, 1.0]), angle)
            )
        )
    )

def relative_pose_from_contact(
    dims_parent, dims_child,
    parent_face_id, child_face_id,
    contact_params
):
    parent_plane = get_contact_planes(dims_parent)[parent_face_id]
    child_plane = get_contact_planes(dims_child)[child_face_id]
    contact_transform = get_contact_transform(contact_params)
    return (parent_plane.dot(contact_transform)).dot(jnp.linalg.inv(child_plane))


## Get poses


def iter(poses, box_dims, edge, contact_params, face_params):
    i, j = edge
    x,y,ang = contact_params
    rel_pose = relative_pose_from_contact(box_dims[i], box_dims[j], face_params[0], face_params[1], (x,y,ang))
    return (
        poses[i].dot(rel_pose) * (i != -1)
        +
        poses[j] * (i == -1)
    )

def get_poses(poses, box_dims, edges, contact_params, face_params):
    def _f(poses, _):
        new_poses = jax.vmap(iter, in_axes=(None, None, 0, 0, 0,))(poses, box_dims, edges, contact_params, face_params)
        return (new_poses, new_poses)
    return jax.lax.scan(_f, poses, jnp.ones(edges.shape[0]))[0]