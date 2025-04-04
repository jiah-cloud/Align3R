# Copyright (C) 2024-present Naver Corporation. All rights reserved.
# Licensed under CC BY-NC-SA 4.0 (non-commercial use only).

# --------------------------------------------------------
# utilitary functions about images (loading/converting...)
# --------------------------------------------------------
import os
import torch
import numpy as np
import PIL.Image
from PIL.ImageOps import exif_transpose
import torchvision.transforms as tvf
os.environ["OPENCV_IO_ENABLE_OPENEXR"] = "1"
import cv2  # noqa
import glob
import imageio
import matplotlib.pyplot as plt

try:
    from pillow_heif import register_heif_opener  # noqa
    register_heif_opener()
    heif_support_enabled = True
except ImportError:
    heif_support_enabled = False

ImgNorm = tvf.Compose([tvf.ToTensor(), tvf.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))])
ToTensor = tvf.ToTensor()
TAG_FLOAT = 202021.25

def depth_read(filename):
    """ Read depth data from file, return as numpy array. """
    f = open(filename,'rb')
    check = np.fromfile(f,dtype=np.float32,count=1)[0]
    assert check == TAG_FLOAT, ' depth_read:: Wrong tag in flow file (should be: {0}, is: {1}). Big-endian machine? '.format(TAG_FLOAT,check)
    width = np.fromfile(f,dtype=np.int32,count=1)[0]
    height = np.fromfile(f,dtype=np.int32,count=1)[0]
    size = width*height
    assert width > 0 and height > 0 and size > 1 and size < 100000000, ' depth_read:: Wrong input size (width = {0}, height = {1}).'.format(width,height)
    depth = np.fromfile(f,dtype=np.float32,count=-1).reshape((height,width))
    return depth

def cam_read(filename):
    """ Read camera data, return (M,N) tuple.
    
    M is the intrinsic matrix, N is the extrinsic matrix, so that

    x = M*N*X,
    with x being a point in homogeneous image pixel coordinates, X being a
    point in homogeneous world coordinates.
    """
    f = open(filename,'rb')
    check = np.fromfile(f,dtype=np.float32,count=1)[0]
    assert check == TAG_FLOAT, ' cam_read:: Wrong tag in flow file (should be: {0}, is: {1}). Big-endian machine? '.format(TAG_FLOAT,check)
    M = np.fromfile(f,dtype='float64',count=9).reshape((3,3))
    N = np.fromfile(f,dtype='float64',count=12).reshape((3,4))
    return M,N

def flow_read(filename):
    """ Read optical flow from file, return (U,V) tuple. 
    
    Original code by Deqing Sun, adapted from Daniel Scharstein.
    """
    f = open(filename,'rb')
    check = np.fromfile(f,dtype=np.float32,count=1)[0]
    assert check == TAG_FLOAT, ' flow_read:: Wrong tag in flow file (should be: {0}, is: {1}). Big-endian machine? '.format(TAG_FLOAT,check)
    width = np.fromfile(f,dtype=np.int32,count=1)[0]
    height = np.fromfile(f,dtype=np.int32,count=1)[0]
    size = width*height
    assert width > 0 and height > 0 and size > 1 and size < 100000000, ' flow_read:: Wrong input size (width = {0}, height = {1}).'.format(width,height)
    tmp = np.fromfile(f,dtype=np.float32,count=-1).reshape((height,width*2))
    u = tmp[:,np.arange(width)*2]
    v = tmp[:,np.arange(width)*2 + 1]
    return u,v

def img_to_arr( img ):
    if isinstance(img, str):
        img = imread_cv2(img)
    return img

def imread_cv2(path, options=cv2.IMREAD_COLOR):
    """ Open an image or a depthmap with opencv-python.
    """
    if path.endswith(('.exr', 'EXR')):
        options = cv2.IMREAD_ANYDEPTH
    img = cv2.imread(path, options)
    if img is None:
        raise IOError(f'Could not load image={path} with {options=}')
    if img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return img


def rgb(ftensor, true_shape=None):
    if isinstance(ftensor, list):
        return [rgb(x, true_shape=true_shape) for x in ftensor]
    if isinstance(ftensor, torch.Tensor):
        ftensor = ftensor.detach().cpu().numpy()  # H,W,3
    if ftensor.ndim == 3 and ftensor.shape[0] == 3:
        ftensor = ftensor.transpose(1, 2, 0)
    elif ftensor.ndim == 4 and ftensor.shape[1] == 3:
        ftensor = ftensor.transpose(0, 2, 3, 1)
    if true_shape is not None:
        H, W = true_shape
        ftensor = ftensor[:H, :W]
    if ftensor.dtype == np.uint8:
        img = np.float32(ftensor) / 255
    else:
        img = (ftensor * 0.5) + 0.5
    return img.clip(min=0, max=1)


def _resize_pil_image(img, long_edge_size, nearest=False):
    S = max(img.size)
    if S > long_edge_size:
        interp = PIL.Image.LANCZOS if not nearest else PIL.Image.NEAREST
    elif S <= long_edge_size:
        interp = PIL.Image.BICUBIC
    new_size = tuple(int(round(x*long_edge_size/S)) for x in img.size)
    return img.resize(new_size, interp)

def resize_numpy_image(img, long_edge_size):
    """
    Resize the NumPy image to a specified long edge size using OpenCV.
    
    Args:
    img (numpy.ndarray): Input image with shape (H, W, C).
    long_edge_size (int): The size of the long edge after resizing.
    
    Returns:
    numpy.ndarray: The resized image.
    """
    # Get the original dimensions of the image
    h, w = img.shape[:2]
    S = max(h, w)

    # Choose interpolation method
    if S > long_edge_size:
        interp = cv2.INTER_LANCZOS4
    else:
        interp = cv2.INTER_CUBIC
    
    # Calculate the new size
    new_size = (int(round(w * long_edge_size / S)), int(round(h * long_edge_size / S)))
    
    # Resize the image
    resized_img = cv2.resize(img, new_size, interpolation=interp)
    
    return resized_img

def crop_center(img, crop_width, crop_height):
    """
    Crop the center of the image.
    
    Args:
    img (numpy.ndarray): Input image with shape (H, W) or (H, W, C).
    crop_width (int): The width of the cropped area.
    crop_height (int): The height of the cropped area.
    
    Returns:
    numpy.ndarray: The cropped image.
    """
    h, w = img.shape[:2]
    cx, cy = h // 2, w // 2
    x1 = max(cx - crop_height // 2, 0)
    x2 = min(cx + crop_height // 2, h)
    y1 = max(cy - crop_width // 2, 0)
    y2 = min(cy + crop_width // 2, w)
    
    cropped_img = img[x1:x2, y1:y2]
    
    return cropped_img
    
def crop_img(img, size, pred_depth=None, square_ok=False, nearest=False, crop=True):
    W1, H1 = img.size
    if size == 224:
        # resize short side to 224 (then crop)
        img = _resize_pil_image(img, round(size * max(W1/H1, H1/W1)), nearest=nearest)
        if pred_depth is not None:
            pred_depth = resize_numpy_image(pred_depth, round(size * max(W1 / H1, H1 / W1)))
    else:
        # resize long side to 512
        img = _resize_pil_image(img, size, nearest=nearest)
        if pred_depth is not None:
            pred_depth = resize_numpy_image(pred_depth, size)
    W, H = img.size
    cx, cy = W//2, H//2
    if size == 224:
        half = min(cx, cy)
        img = img.crop((cx-half, cy-half, cx+half, cy+half))
        if pred_depth is not None:
            pred_depth = crop_center(pred_depth, 2 * half, 2 * half)   
    else:
        halfw, halfh = ((2*cx)//16)*8, ((2*cy)//16)*8
        if not (square_ok) and W == H:
            halfh = 3*halfw/4
        if crop:
            img = img.crop((cx-halfw, cy-halfh, cx+halfw, cy+halfh))
            if pred_depth is not None:
                pred_depth = crop_center(pred_depth, 2 * halfw, 2 * halfh)
        else: # resize
            img = img.resize((2*halfw, 2*halfh), PIL.Image.LANCZOS)
            if pred_depth is not None:
                pred_depth = cv2.resize(pred_depth, (2*halfw, 2*halfh), interpolation=cv2.INTER_CUBIC)
    return img, pred_depth

def pixel_to_pointcloud(depth_map, focal_length_px):
    """
    Convert a depth map to a 3D point cloud.

    Args:
    depth_map (numpy.ndarray): The input depth map with shape (H, W), where each value represents the depth at that pixel.
    focal_length_px (float): The focal length of the camera in pixels.

    Returns:
    numpy.ndarray: The resulting point cloud with shape (H, W, 3), where each point is represented by (X, Y, Z).
    """
    height, width = depth_map.shape
    cx = width / 2
    cy = height / 2

    # Create meshgrid for pixel coordinates
    u = np.arange(width)
    v = np.arange(height)
    u, v = np.meshgrid(u, v)
    #depth_map[depth_map>100]=0
    # Convert pixel coordinates to camera coordinates
    Z = depth_map
    X = (u - cx) * Z / focal_length_px
    Y = (v - cy) * Z / focal_length_px
    
    # Stack the coordinates into a point cloud (H, W, 3)
    point_cloud = np.dstack((X, Y, Z)).astype(np.float32)
    point_cloud = normalize_pointcloud(point_cloud)
    # Optional: Filter out invalid depth values (if necessary)
    # point_cloud = point_cloud[depth_map > 0]
    #print(point_cloud)
    return point_cloud

def normalize_pointcloud(point_cloud):
    min_vals = np.min(point_cloud, axis=(0, 1))
    max_vals = np.max(point_cloud, axis=(0, 1))
    #print(min_vals, max_vals)
    normalized_point_cloud = (point_cloud - min_vals) / (max_vals - min_vals)
    return normalized_point_cloud

def load_images(folder_or_list, size, square_ok=False, verbose=True, dynamic_mask_root=None, crop=True, fps=0, traj_format="sintel", start=0, interval=30, depth_prior_name='depthpro'):
    """Open and convert all images or videos in a list or folder to proper input format for DUSt3R."""
    if isinstance(folder_or_list, str):
        if verbose:
            print(f'>> Loading images from {folder_or_list}')
        # if folder_or_list is a folder, load all images in the folder
        if os.path.isdir(folder_or_list):
            root, folder_content = folder_or_list, sorted(os.listdir(folder_or_list))
        else: # the folder_content will be the folder_or_list itself
            root, folder_content = '', [folder_or_list]

    elif isinstance(folder_or_list, list):
        if verbose:
            print(f'>> Loading a list of {len(folder_or_list)} items')
        root, folder_content = '', folder_or_list

    else:
        raise ValueError(f'Bad input {folder_or_list=} ({type(folder_or_list)})')

    supported_images_extensions = ['.jpg', '.jpeg', '.png']
    supported_video_extensions = ['.mp4', '.avi', '.mov']
    if heif_support_enabled:
        supported_images_extensions += ['.heic', '.heif']
    supported_images_extensions = tuple(supported_images_extensions)
    supported_video_extensions = tuple(supported_video_extensions)

    imgs = []
    imgs_raw = []
    # Sort items by their names
    #start = 0
    folder_content = sorted(folder_content, key=lambda x: x.split('/')[-1])[start : start + interval]
    # print(start,interval,len(folder_content))
    for path in folder_content:
        full_path = os.path.join(root, path)
        if path.lower().endswith(supported_images_extensions):
            # Process image files
            img = exif_transpose(PIL.Image.open(full_path)).convert('RGB')
            imgs_raw.append(img)
            if traj_format == 'sintel':
              pred_depth = np.load(full_path.replace('clean','depth_prediction_' + depth_prior_name).replace('.png', '.npz'))
            elif traj_format in ["tum", "tartanair"]:
              pred_depth = np.load(full_path.replace('rgb_50','rgb_50_depth_prediction_' + depth_prior_name).replace('.png', '.npz'))
            elif traj_format in ["bonn"]:
                pred_depth = np.load(full_path.replace('rgb_110','rgb_110_depth_prediction_' + depth_prior_name).replace('.png', '.npz'))
            elif traj_format in ["davis"]:
                pred_depth = np.load(full_path.replace('JPEGImages','depth_prediction_' + depth_prior_name).replace('.jpg', '.npz').replace('480p', '1080p'))
            elif traj_format in ["scannet"]:
                pred_depth = np.load(full_path.replace('color_30','color_90_depth_prediction_' + depth_prior_name).replace('.jpg', '.npz').replace('.png', '.npz'))
            elif traj_format in ["kitti"]:
                pred_depth = np.load(full_path.replace('image_gathered','depth_prediction_' + depth_prior_name).replace('.jpg', '.npz').replace('.png', '.npz'))
            else:
                pred_depth = np.load(full_path.replace('.png','_pred_depth_' + depth_prior_name + '.npz').replace('.jpg','_pred_depth_' + depth_prior_name + '.npz'), allow_pickle=True)
            #print(pred_depth)
            if depth_prior_name == 'depthpro':
              focal_length_px = pred_depth['focallength_px']
            else:
              focal_length_px = 200
            pred_depth1 = pred_depth['depth']

            if len(pred_depth1.shape) == 3:
                pred_depth1 = np.squeeze(pred_depth1)

            pred_depth = pixel_to_pointcloud(pred_depth1, focal_length_px)
            W1, H1 = img.size
            img, pred_depth = crop_img(img, size, pred_depth, square_ok=square_ok, crop=crop)
            W2, H2 = img.size

            if verbose:
                print(f' - Adding {path} with resolution {W1}x{H1} --> {W2}x{H2}')
            
            single_dict = dict(
                img=ImgNorm(img)[None],
                pred_depth=pred_depth[None,...],
                true_shape=np.int32([img.size[::-1]]),
                idx=len(imgs),
                instance=full_path,
                mask=~(ToTensor(img)[None].sum(1) <= 0.01)
            )
            
            if dynamic_mask_root is not None:
                dynamic_mask_path = os.path.join(dynamic_mask_root, os.path.basename(path))
            else:  # Sintel dataset handling
                dynamic_mask_path = full_path.replace('final', 'dynamic_label_perfect').replace('clean', 'dynamic_label_perfect').replace('MPI-Sintel-training_images','MPI-Sintel-depth-training')
            #print(dynamic_mask_path)
            if os.path.exists(dynamic_mask_path):
                dynamic_mask = PIL.Image.open(dynamic_mask_path).convert('L')
                dynamic_mask, _ = crop_img(dynamic_mask, size, square_ok=square_ok)
                #print(dynamic_mask)
                dynamic_mask = ToTensor(dynamic_mask)[None].sum(1) > 0.99  # "1" means dynamic
                single_dict['dynamic_mask'] = dynamic_mask
                # if dynamic_mask.sum() < 0.8 * dynamic_mask.numel():  # Consider static if over 80% is dynamic
                #     single_dict['dynamic_mask'] = dynamic_mask
                # else:
                #     single_dict['dynamic_mask'] = torch.zeros_like(single_dict['mask'])
            else:
                single_dict['dynamic_mask'] = torch.zeros_like(single_dict['mask'])

            imgs.append(single_dict)

        elif path.lower().endswith(supported_video_extensions):
            # Process video files
            if verbose:
                print(f'>> Loading video from {full_path}')
            cap = cv2.VideoCapture(full_path)
            if not cap.isOpened():
                print(f'Error opening video file {full_path}')
                continue

            video_fps = cap.get(cv2.CAP_PROP_FPS)
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

            if video_fps == 0:
                print(f'Error: Video FPS is 0 for {full_path}')
                cap.release()
                continue
            if fps > 0:
                frame_interval = max(1, int(round(video_fps / fps)))
            else:
                frame_interval = 1
            frame_indices = list(range(0, total_frames, frame_interval))
            if interval is not None:
                frame_indices = frame_indices[:interval]

            if verbose:
                print(f' - Video FPS: {video_fps}, Frame Interval: {frame_interval}, Total Frames to Read: {len(frame_indices)}')

            for frame_idx in frame_indices:
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                ret, frame = cap.read()
                if not ret:
                    break  # End of video

                img = PIL.Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                W1, H1 = img.size
                img, _ = crop_img(img, size, square_ok=square_ok, crop=crop)
                W2, H2 = img.size

                if verbose:
                    print(f' - Adding frame {frame_idx} from {path} with resolution {W1}x{H1} --> {W2}x{H2}')
                
                single_dict = dict(
                    img=ImgNorm(img)[None],
                    true_shape=np.int32([img.size[::-1]]),
                    idx=len(imgs),
                    instance=f'{full_path}_frame_{frame_idx}',
                    mask=~(ToTensor(img)[None].sum(1) <= 0.01)
                )

                # Dynamic masks for video frames are set to zeros by default
                single_dict['dynamic_mask'] = torch.zeros_like(single_dict['mask'])

                imgs.append(single_dict)

            cap.release()

        else:
            continue  # Skip unsupported file types

    assert imgs, 'No images found at ' + root
    if verbose:
        print(f' (Found {len(imgs)} images)')
    return imgs, imgs_raw

def enlarge_seg_masks(folder, kernel_size=5, prefix="dynamic_mask"):
    mask_pathes = glob.glob(f'{folder}/{prefix}_*.png')
    for mask_path in mask_pathes:
        mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
        kernel = np.ones((kernel_size, kernel_size),np.uint8)
        enlarged_mask = cv2.dilate(mask, kernel, iterations=1)
        cv2.imwrite(mask_path.replace(prefix, 'enlarged_dynamic_mask'), enlarged_mask)

def show_mask(mask, ax, obj_id=None, random_color=False):
    if random_color:
        color = np.concatenate([np.random.random(3), np.array([0.6])], axis=0)
    else:
        cmap = plt.get_cmap("tab10")
        cmap_idx = 1 if obj_id is None else obj_id
        color = np.array([*cmap(cmap_idx)[:3], 0.6])
    h, w = mask.shape[-2:]
    mask_image = mask.reshape(h, w, 1) * color.reshape(1, 1, -1)
    ax.imshow(mask_image)

def get_overlaied_gif(folder, img_format="frame_*.png", mask_format="dynamic_mask_*.png", output_path="_overlaied.gif"):
    img_paths = glob.glob(f'{folder}/{img_format}')
    mask_paths = glob.glob(f'{folder}/{mask_format}')
    assert len(img_paths) == len(mask_paths), f"Number of images and masks should be the same, got {len(img_paths)} images and {len(mask_paths)} masks"
    img_paths = sorted(img_paths)
    mask_paths = sorted(mask_paths, key=lambda x: int(x.split('_')[-1].split('.')[0]))
    frames = []
    for img_path, mask_path in zip(img_paths, mask_paths):
        # Read image and convert to RGB for Matplotlib
        img = cv2.imread(img_path)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        # Read mask and normalize
        mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
        mask = mask.astype(np.float32) / 255.0
        # Create figure and axis
        fig, ax = plt.subplots(figsize=(img.shape[1]/100, img.shape[0]/100), dpi=100)
        ax.imshow(img)
        # Overlay mask using show_mask
        show_mask(mask, ax)
        ax.axis('off')
        # Render the figure to a numpy array
        fig.canvas.draw()
        img_array = np.frombuffer(fig.canvas.tostring_rgb(), dtype=np.uint8)
        img_array = img_array.reshape(fig.canvas.get_width_height()[::-1] + (3,))
        frames.append(img_array)
        plt.close(fig)  # Close the figure to free memory
    # Save frames as a GIF using imageio
    imageio.mimsave(os.path.join(folder,output_path), frames, fps=10)
