import numpy as np
import jax.numpy as jnp
import jax
from jax3dp3.model import make_scoring_function
from jax3dp3.rendering import render_planes
from jax3dp3.distributions import VonMisesFisher
from jax3dp3.viz.gif import make_gif
from jax3dp3.likelihood import threedp3_likelihood
from jax3dp3.utils import (
    make_centered_grid_enumeration_3d_points,
    quaternion_to_rotation_matrix,
    depth_to_coords_in_camera
)
from jax.scipy.stats.multivariate_normal import logpdf
from jax.scipy.special import logsumexp
from jax3dp3.shape import get_cube_shape
import time
from PIL import Image
from scipy.spatial.transform import Rotation as R
import matplotlib.pyplot as plt
import cv2

h, w, fx_fy, cx_cy = (
    120,
    160,
    jnp.array([200.0, 200.0]),
    jnp.array([80.0, 60.0]),
)
r = 0.2
outlier_prob = 0.01

shape = get_cube_shape(0.5)

render_from_x = lambda x: render_planes(jnp.array([
    [1.0, 0.0, 0.0, x],   
    [0.0, 1.0, 0.0, 0.0],   
    [0.0, 0.0, 1.0, 2.0],   
    [0.0, 0.0, 0.0, 1.0],   
    ]
),shape,h,w,fx_fy,cx_cy)
render_from_x_jit = jax.jit(render_from_x)
render_planes_parallel_jit = jax.jit(jax.vmap(lambda x: render_from_x(x)))

ground_truth_ys = jnp.linspace(-1.0, 1.0, 40)
ground_truth_images = render_planes_parallel_jit(ground_truth_ys)
print('ground_truth_images.shape ',ground_truth_images.shape)
make_gif(ground_truth_images, 5.0, "aismc_data.gif")

def likelihood(x, obs):
    rendered_image = render_from_x(x)
    weight = threedp3_likelihood(obs, rendered_image, r, outlier_prob)
    return weight
likelihood_parallel = jax.vmap(likelihood, in_axes = (0, None))
likelihood_parallel_jit = jax.jit(likelihood_parallel)
print('likelihood(-1.0, ground_truth_images[0,:,:]) ',likelihood(-1.0, ground_truth_images[0,:,:]))

categorical_vmap = jax.vmap(jax.random.categorical, in_axes=(None, 0))

logsumexp_vmap = jax.vmap(logsumexp)

DRIFT_VAR = 0.1
GRID = jnp.arange(-1.0, 1.01, 0.25)

def run_inference(initial_particles, gt_images):
    def particle_filtering_step(data, gt_image):
        particles, weights, key = data
        aux = particles + DRIFT_VAR * jax.random.normal(key, shape=particles.shape)
        aux_plus_grid = aux[:,None] + GRID
        likelihoods = likelihood_parallel_jit(aux_plus_grid.reshape(-1), gt_image).reshape(aux_plus_grid.shape)
        transition_prob = logpdf((aux_plus_grid - particles[:,None])/DRIFT_VAR, 0.0, 1.0)
        s = likelihoods + transition_prob
        s = s - logsumexp_vmap(s)[:,None]
        j = categorical_vmap(key, s) 

        prop = aux_plus_grid[jnp.arange(particles.shape[0]),j]

        r = logpdf((prop[:, None] + GRID)/DRIFT_VAR, 0.0, 1.0)
        r = r - logsumexp_vmap(r)[:,None]
        i = categorical_vmap(key, r) 

        weights = weights + (
            likelihood_parallel_jit(prop,  gt_image)
            + logpdf((prop - particles)/DRIFT_VAR, 0.0, 1.0)
            - s[jnp.arange(particles.shape[0]),j]
            - logpdf((aux - particles)/DRIFT_VAR, 0.0, 1.0)
            + r[jnp.arange(particles.shape[0]),i]
        )

        parent_idxs = jax.random.categorical(key, weights, shape=weights.shape)
        particles = prop[parent_idxs]
        weights = jnp.full(weights.shape[0],logsumexp(weights) - jnp.log(weights.shape[0]))
        key,_ = jax.random.split(key)
        return (particles, weights, key), particles

    initial_weights = jnp.full(initial_particles.shape, 0.0)
    key = jax.random.PRNGKey(3)
    # initial_keys = jax.random.split(initial_key, initial_particles.shape[0])
    return jax.lax.scan(particle_filtering_step, (initial_particles, initial_weights, key), gt_images)


run_inference_jit = jax.jit(run_inference)
num_particles = 100
particles = jnp.full(num_particles, -1.0)

_,x = run_inference_jit(particles, ground_truth_images)

start = time.time()
_,x = run_inference_jit(particles, ground_truth_images)
end = time.time()
print ("Time elapsed:", end - start)
print ("FPS:", ground_truth_images.shape[0] / (end - start))

inferred_images = render_planes_parallel_jit(x[:,-1])
make_gif(inferred_images, 8.0, "aismc_data_inferred.gif")


from IPython import embed; embed()
