"""Compute depth maps for images in the input folder.
"""
import os
import glob
import torch
import utils
import cv2
import argparse
import numpy as np
import time
import statistics
import plotly.graph_objects as go
import matplotlib.pyplot as plt
from vispy import app, scene
from vispy.util.filter import gaussian_filter
from numba import njit

from torchvision.transforms import Compose
from midas.dpt_depth import DPTDepthModel
from midas.midas_net import MidasNet
from midas.midas_net_custom import MidasNet_small
from midas.transforms import Resize, NormalizeImage, PrepareForNet


def height_to_pixel(m):
    if m > 500:
        return (m/1000),0,0
    return 0,(m/1000),0

@njit
def generate_output_image(input):
    #start = time.monotonic_ns()

    output_img = np.atleast_3d(input)
    output_img = output_img/1000
    zeros = np.zeros_like(output_img)
    output_img = np.dstack((zeros, output_img))
    output_img = np.dstack((output_img, zeros))
    #output_img = np.vectorize(height_to_pixel)(output_img)
    #np.apply_along_axis(height_to_pixel, axis=2, arr = output_img)            

    #end = time.monotonic_ns()

    #print("Running Image Conversion: ", end - start/10000000.0)

    return output_img



def run(input_path, output_path, model_path, model_type="large", optimize=True):
    """Run MonoDepthNN to compute depth maps.

    Args:
        input_path (str): path to input folder
        output_path (str): path to output folder
        model_path (str): path to saved model
    """
    print("initialize")

    # select device
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print("device: %s" % device)

    
    model = MidasNet_small(model_path, features=64, backbone="efficientnet_lite3", exportable=True, non_negative=True, blocks={'expand': True})
    
    net_w, net_h = 256, 256
    resize_mode="upper_bound"
    normalization = NormalizeImage(
        mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]
        )

    # model = DPTDepthModel(
    #         path=model_path,
    #         backbone="vitl16_384",
    #         non_negative=True,
    #     )
    # net_w, net_h = 384, 384
    # resize_mode = "minimal"
    # normalization = NormalizeImage(mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5])
    
    transform = Compose(
        [
            Resize(
                net_w,
                net_h,
                resize_target=None,
                keep_aspect_ratio=True,
                ensure_multiple_of=32,
                resize_method=resize_mode,
                image_interpolation_method=cv2.INTER_CUBIC,
            ),
            normalization,
            PrepareForNet(),
        ]
    )

    model.eval()
    
    if optimize==True:
        # rand_example = torch.rand(1, 3, net_h, net_w)
        # model(rand_example)
        # traced_script_module = torch.jit.trace(model, rand_example)
        # model = traced_script_module
    
        if device == torch.device("cuda"):
            model = model.to(memory_format=torch.channels_last)  
            model = model.half()

    model.to(device)

    # get input
    img_names = glob.glob(os.path.join(input_path, "*"))
    num_images = len(img_names)

    # create output folder
    os.makedirs(output_path, exist_ok=True)

    print("start processing")

    camera = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    #camera = cv2.VideoCapture.open("E:\Projects\Current\Rover\Sandbox\Midas\MiDaS\input\mctest.jpg")

    print("camera set")
    cv2.namedWindow("input", cv2.WINDOW_NORMAL)


    while True:

        # input
        ret, img = camera.read()

        img_input = transform({"image": img})["image"]

        if ret:
            start = time.monotonic_ns()

            with torch.no_grad():
                sample = torch.from_numpy(img_input).to(device).unsqueeze(0)
                if optimize==True and device == torch.device("cuda"):
                    sample = sample.to(memory_format=torch.channels_last)  
                    sample = sample.half()
                prediction = model.forward(sample)
                prediction = (
                    torch.nn.functional.interpolate(
                        prediction.unsqueeze(1),
                        size=img.shape[:2],
                        mode="bicubic",
                        align_corners=False,
                    )
                    .squeeze()
                    .cpu()
                    .numpy()
                )

            end = time.monotonic_ns()

            print("Running Depth Analysis: ", end - start/1000000.0)


            # output_img = generate_output_image(prediction)
            output_image = (prediction/1000)*255

            output = output_image.astype(np.uint8)

            ret, blurred = cv2.threshold(output, 125, 255, cv2.THRESH_BINARY)

            #blurred = cv2.adaptiveThreshold(output,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY,15,5)

            contours, heirarchy = cv2.findContours(image=blurred, mode=cv2.RETR_TREE, method=cv2.CHAIN_APPROX_SIMPLE)

            cv2.drawContours(img, contours, -1, (0,255,0), 3)

            # (minVal, maxVal, minLoc, maxLoc) = cv2.minMaxLoc(blurred)
            # cv2.circle(img, maxLoc, 20, (255,0,0), 2)

            # print(output_img)
            cv2.imshow("input", img)
            cv2.imshow("blurred", blurred)
            cv2.imshow('output', output)

            k = cv2.waitKey(1)
            if k%256 == 27:
                # ESC pressed
                print("Escape hit, closing...")
                break

    
    print("finished")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument('-i', '--input_path', 
        default='input',
        help='folder with input images'
    )

    parser.add_argument('-o', '--output_path', 
        default='output',
        help='folder for output images'
    )

    parser.add_argument('-m', '--model_weights', 
        default=None,
        help='path to the trained weights of model'
    )

    parser.add_argument('-t', '--model_type', 
        default='dpt_large',
        help='model type: dpt_large, dpt_hybrid, midas_v21_large or midas_v21_small'
    )

    parser.add_argument('--optimize', dest='optimize', action='store_true')
    parser.add_argument('--no-optimize', dest='optimize', action='store_false')
    parser.set_defaults(optimize=True)

    args = parser.parse_args()

    default_models = {
        "midas_v21_small": "weights/midas_v21_small-70d6b9c8.pt",
        "midas_v21": "weights/midas_v21-f6b98070.pt",
        "dpt_large": "weights/dpt_large-midas-2f21e586.pt",
        "dpt_hybrid": "weights/dpt_hybrid-midas-501f0c75.pt",
    }

    if args.model_weights is None:
        args.model_weights = "weights/midas_v21_small-70d6b9c8.pt" #default_models[args.model_type]

    # set torch options
    torch.backends.cudnn.enabled = True
    torch.backends.cudnn.benchmark = True

    # compute depth maps
    run(args.input_path, args.output_path, args.model_weights, args.model_type, args.optimize)
