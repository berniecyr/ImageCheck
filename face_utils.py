import os
import cv2


def save_face_crop(
    original_frame,
    image_base_name,
    frame_idx,
    name,
    abs_top,
    abs_bottom,
    abs_left,
    abs_right,
    h_orig,
    w_orig,
    scale,
    faces_dir,
    date_str=None,
    time_str=None,
    update_image_exif=None
):
    target_folder = os.path.join(
        faces_dir,
        name if name != "Unknown" else "UNKNOWN_PERSON"
    )

    os.makedirs(target_folder, exist_ok=True)

    fname = f"{image_base_name}_face.jpg"

    face_save_path = os.path.join(target_folder, fname)

    if os.path.exists(face_save_path):

        fname = f"{image_base_name}_f{frame_idx}_face.jpg"

        face_save_path = os.path.join(target_folder, fname)

        counter = 1

        while os.path.exists(face_save_path):
            fname = f"{image_base_name}_f{frame_idx}_{counter}_face.jpg"

            face_save_path = os.path.join(target_folder, fname)

            counter += 1

    orig_top = max(0, int(abs_top / scale))
    orig_bottom = min(h_orig, int(abs_bottom / scale))

    orig_left = max(0, int(abs_left / scale))
    orig_right = min(w_orig, int(abs_right / scale))

    cv2.imwrite(
        face_save_path,
        original_frame[orig_top:orig_bottom, orig_left:orig_right]
    )

    if date_str and update_image_exif:
        update_image_exif(
            face_save_path,
            date_str,
            time_str
        )

    return face_save_path, (
        orig_left,
        orig_top,
        orig_right,
        orig_bottom
    )