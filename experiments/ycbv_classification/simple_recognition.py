import time
import trimesh
import os
import numpy as np
import jax.numpy as jnp
import jax

import jax3dp3
import jax3dp3.transforms_3d as t3d

h, w = 120, 160
fx,fy = 200.0, 200.0
cx,cy = 80.0, 60.0
near=0.01
far=50.0
max_depth=2.0

jax3dp3.setup_renderer(h, w, fx, fy, cx, cy, near, far)

model_dir = os.path.join(jax3dp3.utils.get_assets_dir(),"models")
model_names = os.listdir(model_dir)
for model in model_names:
    mesh = trimesh.load(os.path.join(jax3dp3.utils.get_assets_dir(),"models/{}/textured_simple.obj".format(model)))
    jax3dp3.load_model(mesh)

gt_model_idx = 10
gt_mesh_name = model_names[gt_model_idx]

center_of_sampling = t3d.transform_from_pos(jnp.array([0.0, 0.0, 0.5]))
variance = 0.0000001
concentration = 0.01
key = jax.random.PRNGKey(10)
sampler_jit = jax.jit(jax3dp3.distributions.gaussian_vmf_sample)
gt_pose = sampler_jit(key, center_of_sampling, variance, concentration)
gt_image = jax3dp3.render_single_object(gt_pose, gt_model_idx)
jax3dp3.viz.save_depth_image(gt_image[:,:,2], "gt_image.png", max=max_depth)

def scorer(rendered_image, gt, r, outlier_prob):
    weight = jax3dp3.likelihood.threedp3_likelihood(gt, rendered_image, r, outlier_prob)
    return weight
scorer_parallel = jax.vmap(scorer, in_axes=(0, None, None, None))
scorer_parallel_jit = jax.jit(scorer_parallel)

non_zero_points = gt_image[gt_image[:,:,2]>0,:3]
_, centroid_pose = jax3dp3.utils.axis_aligned_bounding_box(non_zero_points)
rotation_deltas = jax3dp3.enumerations.make_rotation_grid_enumeration(50, 20)
poses_to_score = jnp.einsum("ij,ajk->aik", centroid_pose, rotation_deltas)

object_indices = list(range(len(model_names)))

import jax.experimental.host_callback as hcb

dummy = jnp.zeros((1000,h,w,4))

def f(p, device):
    return jnp.zeros((1000,h,w,4))

_ret = jax3dp3.render_multi(poses_to_score,h,w,0)

import numpy as np

def rm_wrapper(poses_idx, device=None):
    poses, idx = poses_idx
    result = jax3dp3.render_multi(jnp.asarray(poses),h,w,idx)
    return result
# rm_wrapper((poses_to_score, 0))

# def callback_renderer(poses_to_score, idx):
#     return hcb.call(rm_wrapper, (poses_to_score, idx), result_shape=_ret, call_with_device=True)
# callback_renderer_jit = jax.jit(callback_renderer)

def callback_renderer(poses_to_score, idx):
    return jax.pure_callback(rm_wrapper, 
        jax.ShapeDtypeStruct((poses_to_score.shape[0],h,w,4),'float32'), 
        (poses_to_score, idx))


vmap_wrapper = jax.vmap(rm_wrapper, in_axes=((None, (0,)),))  # fails
vmap_cbr = jax.vmap(callback_renderer, in_axes=(None,0))

vmap_cbr_jit = jax.jit(vmap_cbr)


from IPython import embed; embed()

hcb.call(rm_wrapper, poses_to_score, 
        result_shape=jax.ShapeDtypeStruct((1000,h,w,4), poses_to_score.dtype),call_with_device=True)


ret = hcb.call(rm_wrapper, 
        (poses_to_score, 0), 
        result_shape=(poses_to_score.shape[0], h, w, 4, poses_to_score.dtype),
        call_with_device=True)

from IPython import embed; embed()

start= time.time()
all_scores = [] 
for idx in object_indices:
    images = jax3dp3.render_parallel(poses_to_score, idx)
    weights = scorer_parallel_jit(images, gt_image, 0.01, 0.2)
    best_pose_idx = weights.argmax()
    jax3dp3.viz.save_depth_image(images[best_pose_idx,:,:,2], "imgs/best_{}.png".format(model_names[idx]), max=max_depth)
    all_scores.append(weights[best_pose_idx])
print(gt_mesh_name)
print(model_names[np.argmax(all_scores)])
end= time.time()
print ("Time elapsed:", end - start)

print(np.array(model_names)[np.argsort(all_scores)])
print(np.array(all_scores)[np.argsort(all_scores)])



from IPython import embed; embed()
