// Copyright (c) 2020, NVIDIA CORPORATION.  All rights reserved.
//
// NVIDIA CORPORATION and its licensors retain all intellectual property
// and proprietary rights in and to this software, related documentation
// and any modifications thereto.  Any use, reproduction, disclosure or
// distribution of this software and related documentation without an express
// license agreement from NVIDIA CORPORATION is strictly prohibited.

#include "torch_common.inl"
#include "torch_types.h"
#include "../common/common.h"
#include "../common/rasterize_gl.h"

#include <tuple>

//------------------------------------------------------------------------
// Python GL state wrapper methods.

RasterizeGLStateWrapper::RasterizeGLStateWrapper(bool enableDB, bool automatic_, int cudaDeviceIdx_)
{
    pState = new RasterizeGLState();
    automatic = automatic_;
    cudaDeviceIdx = cudaDeviceIdx_;
    memset(pState, 0, sizeof(RasterizeGLState));
    pState->enableDB = enableDB ? 1 : 0;
    rasterizeInitGLContext(NVDR_CTX_PARAMS, *pState, cudaDeviceIdx_);
    releaseGLContext();
}

RasterizeGLStateWrapper::~RasterizeGLStateWrapper(void)
{
    setGLContext(pState->glctx);
    rasterizeReleaseBuffers(NVDR_CTX_PARAMS, *pState);
    releaseGLContext();
    destroyGLContext(pState->glctx);
    delete pState;
}

void RasterizeGLStateWrapper::setContext(void)
{
    setGLContext(pState->glctx);
}

void RasterizeGLStateWrapper::releaseContext(void)
{
    releaseGLContext();
}

//------------------------------------------------------------------------
// Forward op (OpenGL).

void threedp3_likelihood(float *pos, float time, int width, int height, int depth);

std::tuple<torch::Tensor, torch::Tensor> rasterize_fwd_gl(RasterizeGLStateWrapper& stateWrapper,  torch::Tensor proj, torch::Tensor pos, torch::Tensor tri, std::tuple<int, int> resolution, torch::Tensor ranges, int peeling_idx)
{
    const at::cuda::OptionalCUDAGuard device_guard(device_of(pos));
    cudaStream_t stream = at::cuda::getCurrentCUDAStream();
    RasterizeGLState& s = *stateWrapper.pState;

    // Check inputs.
    NVDR_CHECK_DEVICE(pos, tri);
    NVDR_CHECK_CPU(ranges);
    NVDR_CHECK_CONTIGUOUS(pos, tri, ranges);
    NVDR_CHECK_F32(pos);
    NVDR_CHECK_I32(tri, ranges);

    // Check that GL context was created for the correct GPU.
    NVDR_CHECK(pos.get_device() == stateWrapper.cudaDeviceIdx, "GL context must must reside on the same device as input tensors");

    // Determine number of outputs
    int num_outputs = s.enableDB ? 2 : 1;

    // Determine instance mode and check input dimensions.
    bool instance_mode = pos.sizes().size() > 2;
    if (instance_mode)
        NVDR_CHECK(pos.sizes().size() == 3 && pos.size(0) > 0 && pos.size(1) > 0 && pos.size(2) == 4, "instance mode - pos must have shape [>0, >0, 4]");
    else
    {
        NVDR_CHECK(pos.sizes().size() == 2 && pos.size(0) > 0 && pos.size(1) == 4, "range mode - pos must have shape [>0, 4]");
        NVDR_CHECK(ranges.sizes().size() == 2 && ranges.size(0) > 0 && ranges.size(1) == 2, "range mode - ranges must have shape [>0, 2]");
    }
    NVDR_CHECK(tri.sizes().size() == 2 && tri.size(0) > 0 && tri.size(1) == 3, "tri must have shape [>0, 3]");

    // Get output shape.
    int height = std::get<0>(resolution);
    int width  = std::get<1>(resolution);
    int depth  = instance_mode ? pos.size(0) : ranges.size(0);
    NVDR_CHECK(height > 0 && width > 0, "resolution must be [>0, >0]");

    // Get position and triangle buffer sizes in int32/float32.
    int posCount = 4 * pos.size(0) * (instance_mode ? pos.size(1) : 1);
    int triCount = 3 * tri.size(0);

    // Set the GL context unless manual context.
    if (stateWrapper.automatic)
        setGLContext(s.glctx);

    // Resize all buffers.
    bool changes = false;
    rasterizeResizeBuffers(NVDR_CTX_PARAMS, s, changes, posCount, triCount, width, height, depth);
    if (changes)
    {
#ifdef _WIN32
        // Workaround for occasional blank first frame on Windows.
        releaseGLContext();
        setGLContext(s.glctx);
#endif
    }

    // Copy input data to GL and render.
    const float* projPtr = proj.data_ptr<float>();
    const float* posPtr = pos.data_ptr<float>();
    const int32_t* rangesPtr = instance_mode ? 0 : ranges.data_ptr<int32_t>(); // This is in CPU memory.
    const int32_t* triPtr = tri.data_ptr<int32_t>();
    int vtxPerInstance = instance_mode ? pos.size(1) : 0;
    rasterizeRender(NVDR_CTX_PARAMS, s, stream, projPtr, posPtr, posCount, vtxPerInstance, triPtr, triCount, rangesPtr, width, height, depth, peeling_idx);

    unsigned int bytes = depth*height*width*4*sizeof(float);

    float* outputPtr[2];
    outputPtr[1] = NULL;

    float *da;
    cudaMalloc((void**)&da, bytes);

    float likelihood;
    cudaMalloc((void**)&da, bytes);

    outputPtr[0] = da;
    rasterizeCopyResults(NVDR_CTX_PARAMS, s, stream, outputPtr, width, height, depth);

    // float* ha = (float*)malloc(bytes);
    // cudaMemcpy(ha, da, bytes, cudaMemcpyDeviceToHost);
    // std::cout << "ha[0] : " << ha[0] << std::endl;
    // std::cout << "ha[1] : " << ha[1] << std::endl;
    // std::cout << "ha[2] : " << ha[2] << std::endl;
    // std::cout << "ha[3] : " << ha[3] << std::endl;
    // std::cout << "ha[4] : " << ha[4] << std::endl;

    std::cout << height << " " << width << " " << depth << " !!" << std::endl;

    dim3 blockSize(depth, 1, 1);
    dim3 gridSize(height,width, 1);
    float r = 4.0;
    void* args[] = {&da, &r, &width, &height, &depth};
    // cudaLaunchKernel((void*)threedp3_likelihood, gridSize, blockSize, args, 0, stream);


    // Allocate output tensors.
    torch::TensorOptions opts = torch::TensorOptions().dtype(torch::kFloat32).device(torch::kCUDA);
    torch::Tensor out = torch::empty({depth* height * width * 4}, opts);
    torch::Tensor out_db = torch::empty({depth, height, width, s.enableDB ? 4 : 0}, opts);
    
    cudaMemcpy(out.data_ptr<float>(), da, bytes, cudaMemcpyDeviceToDevice);


    // float* outputPtr2[2];
    // outputPtr2[0] = out.data_ptr<float>();
    // outputPtr2[1] = NULL;

    // rasterizeCopyResults(NVDR_CTX_PARAMS, s, stream, outputPtr2, width, height, depth);

    // Done. Release GL context and return.
    if (stateWrapper.automatic)
        releaseGLContext();

    return std::tuple<torch::Tensor, torch::Tensor>(out, out_db);
}



//------------------------------------------------------------------------
