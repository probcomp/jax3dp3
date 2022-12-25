
import jax.numpy as jnp
from .transforms_3d import apply_transform
import functools
import jax

# render single object model cloud at specified pose to a "coordinate image"
# "coordinate image" is (h,w,3) where at each pixel we have the 3d coordinate of that point
# input_cloud: (N,3) object model point cloud
# pose: (4,4) pose matrix. rotation matrix in top left (3,3) and translation in (:3,3)
# h,w : height and width
# fx_fy : focal lengths
# cx_cy : principal point
# output: (h,w,3) coordinate image
# @functools.partial(jax.jit, static_argnames=["h","w"])

def render_cloud_at_pose(input_cloud, pose, h, w, fx, fy, cx, cy, pixel_smudge):
    transformed_cloud = apply_transform(input_cloud, pose)
    point_cloud = jnp.vstack([-1.0 * jnp.ones((1, 3)), transformed_cloud])

    point_cloud_normalized = point_cloud / point_cloud[:, 2].reshape(-1, 1)
    temp1 = point_cloud_normalized[:, :2] * jnp.array([fx,fy])
    temp2 = temp1 + jnp.array([cx,cy])
    pixels = jnp.round(temp2)

    x, y = jnp.meshgrid(jnp.arange(w), jnp.arange(h))
    matches = (jnp.abs(x[:, :, None] - pixels[:, 0]) <= pixel_smudge) & (jnp.abs(y[:, :, None] - pixels[:, 1]) <= pixel_smudge)
    matches = matches * (1000.0 - point_cloud[:,-1][None, None, :])

    a = jnp.argmax(matches, axis=-1)    
    return point_cloud[a]


def render_planes(pose, shape, h,w, fx, fy, cx, cy):
    shape_planes, shape_dimensions = shape
    plane_poses = jnp.einsum("ij,ajk",pose, shape_planes)

    r, c = jnp.meshgrid(jnp.arange(w), jnp.arange(h))
    pixel_coords = jnp.stack([r,c],axis=-1)
    pixel_coords_dir = jnp.concatenate([(pixel_coords - jnp.array([cx,cy])) / jnp.array([fx,fy]), jnp.ones((h,w,1))],axis=-1)

    denoms = jnp.einsum("ijk,ak->ija", pixel_coords_dir , plane_poses[:,:3,2])
    numerators = jnp.einsum("...k,...k", 
        plane_poses[:,:3,3],
        plane_poses[:,:3,2]
    )
    d = numerators / (denoms + 1e-10)
    points_temp = jnp.einsum("...ij,...kj", d[:,:,:,None], pixel_coords_dir[:,:,:,None])
    points = jnp.concatenate([points_temp, jnp.ones((*points_temp.shape[:3],1,))],axis=-1) # (H,W,N,4)
    inv_plane_poses = jnp.linalg.inv(plane_poses)
    points_in_plane_frame = jnp.einsum("...ij,ab...j->ab...i", inv_plane_poses, points)

    valid = jnp.all(jnp.abs(points_in_plane_frame[:,:,:,:2]) < shape_dimensions,axis=-1) # (H,W,N)
    z_vals = (1000.0 - points[:,:,:,2]) * valid
    idxs = jnp.argmax(z_vals, axis=-1)

    points_final = (
        points[jnp.arange(points.shape[0])[:, None], jnp.arange(points.shape[1])[None, :], idxs]
        *
        jnp.any(valid, axis=-1)[:,:,None]
    )

    return points_final



def render_planes_rays(rays, pose, shape):
    shape_planes, shape_dimensions = shape
    plane_poses = jnp.einsum("ij,ajk",pose, shape_planes)

    @functools.partial(
        jnp.vectorize,
        signature='(2,3)->(4)',
        excluded=(1,2,),
    )
    def single_ray_rendering_vmap(ray, plane, shape_dimensions):
        l = ray[0]
        l_0 = ray[1]

        p_0 = plane[:3,3]
        n = plane[:3,2]
        
        numerator = (p_0 - l_0).dot(n)
        denom = l.dot(n)

        d = numerator / (denom + 1e-10)
        points_temp = l_0 + d * l

        inv_plane = jnp.linalg.inv(plane)
        points = jnp.concatenate([points_temp, jnp.ones(1)], axis=-1)
        point_in_plane_frame = inv_plane.dot(points)
        valid = jnp.all(jnp.abs(point_in_plane_frame[:2]) < shape_dimensions,axis=-1)
        return valid * points + 10000.0 * (1.0 - valid)

    points = jax.vmap(single_ray_rendering_vmap, in_axes=(None, 0, 0),out_axes=-2)(rays, plane_poses, shape_dimensions)
    idxs = jnp.argmin(points[:,:,2],axis=-1)

    points_final = points[jnp.arange(points.shape[0]), idxs]
    points_final_final = points_final * (points_final[:,2] < 10000.0)[:,None]
    return points_final_final


def render_planes_multiobject(poses, shape_planes, shape_dims, h,w, fx,fy, cx,cy):
    r, c = jnp.meshgrid(jnp.arange(w), jnp.arange(h))
    pixel_coords = jnp.stack([r,c],axis=-1)
    pixel_coords_dir = jnp.concatenate([(pixel_coords - jnp.array([cx,cy])) /  jnp.array([fx,fy]), jnp.ones((h,w,1))],axis=-1)

    return render_planes_multiobject_rays(poses, shape_planes, shape_dims, pixel_coords_dir)

def render_planes_multiobject_rays(poses, shape_planes, shape_dims, pixel_coords_dir):
    plane_poses = jnp.einsum("...ij,...ajk",poses, shape_planes).reshape(-1, 4, 4)
    shape_dimensions = shape_dims.reshape(-1, 2)


    denoms = jnp.einsum("ijk,ak->ija", pixel_coords_dir , plane_poses[:,:3,2])
    numerators = jnp.einsum("...k,...k", 
        plane_poses[:,:3,3],
        plane_poses[:,:3,2]
    )
    d = numerators / (denoms + 1e-10)
    points_temp = jnp.einsum("...ij,...kj", d[:,:,:,None], pixel_coords_dir[:,:,:,None])
    points = jnp.concatenate([points_temp, jnp.ones((*points_temp.shape[:3],1,))],axis=-1) # (H,W,N,4)
    inv_plane_poses = jnp.linalg.inv(plane_poses)
    points_in_plane_frame = jnp.einsum("...ij,ab...j->ab...i", inv_plane_poses, points)

    valid = jnp.all(jnp.abs(points_in_plane_frame[:,:,:,:2]) < shape_dimensions,axis=-1) * (points[:,:,:,2] > 0.0)# (H,W,N)
    z_vals = (20000.0 - points[:,:,:,2]) * valid
    idxs = jnp.argmax(z_vals, axis=-1)

    points_final = (
        points[jnp.arange(points.shape[0])[:, None], jnp.arange(points.shape[1])[None, :], idxs]
        *
        jnp.any(valid, axis=-1)[:,:,None]
    )

    return points_final


def render_sphere(pose, shape, h,w, fx,fy, cx,cy):
    radius = shape
    center = pose[:3,-1]
    center_norm_square = jnp.linalg.norm(center)**2

    r, c = jnp.meshgrid(jnp.arange(w), jnp.arange(h))
    pixel_coords = jnp.stack([r,c],axis=-1)
    pixel_coords_dir = jnp.concatenate([(pixel_coords  - jnp.array([cx,cy])) /  jnp.array([fx,fy]), jnp.ones((h,w,1))],axis=-1)
    u = pixel_coords_dir / jnp.linalg.norm(pixel_coords_dir,axis=-1, keepdims=True)

    u_dot_c = jnp.einsum("ijk,k->ij", u, -center)

    nabla = u_dot_c**2 - (center_norm_square - radius**2)
    valid = nabla > 0

    d = valid * (-u_dot_c - jnp.sqrt(nabla))
    points = jnp.einsum("...i,...ki", d[:,:,None], u[:,:,:,None])
    points_homogeneous = jnp.concatenate([points, jnp.ones((h,w,1))],axis=-1)
    return points_homogeneous


def render_spheres(poses, radii, h, w,  fx,fy, cx,cy):
    center = poses[:, :3,-1]
    center_norm_square = jnp.linalg.norm(center,axis=-1)**2

    r, c = jnp.meshgrid(jnp.arange(w), jnp.arange(h))
    pixel_coords = jnp.stack([r,c],axis=-1)
    pixel_coords_dir = jnp.concatenate([(pixel_coords - jnp.array([cx,cy])) /  jnp.array([fx,fy]), jnp.ones((h,w,1))],axis=-1)
    u = pixel_coords_dir / jnp.linalg.norm(pixel_coords_dir,axis=-1, keepdims=True)

    u_dot_c = jnp.einsum("ijk,ak->ija", u, -center)

    nabla = u_dot_c**2 - (center_norm_square - radii**2)
    valid = nabla > 0

    d = valid * (-u_dot_c - jnp.sqrt(nabla))
    points = jnp.einsum("...ai,...ki->...ak", d[:,:,:, None], u[:,:,:,None])
    z_vals = (10000.0 - points[:,:,:,2]) * (points[:,:,:,2] > 0.0)
    idxs = jnp.argmax(z_vals, axis=-1)

    points = points[jnp.arange(points.shape[0])[:, None], jnp.arange(points.shape[1])[None, :], idxs]
    points_homogeneous = jnp.concatenate([points, jnp.ones((h,w,1))],axis=-1)
    return points_homogeneous