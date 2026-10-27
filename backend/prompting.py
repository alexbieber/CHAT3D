"""Turns a free-form idea into a prompt that produces 3D-friendly reference images.

TRELLIS.2 works best on a single, fully visible object on a clean background,
lit evenly, with no cropping, text or busy scenery. The image step is steered
toward exactly that so the approved image converts well.
"""

STYLES = {
    "realistic": "photorealistic product render, physically based materials, high detail",
    "stylized": "stylized 3D game asset, clean shapes, hand-painted textures",
    "cartoon": "cute cartoon 3D character style, smooth rounded forms, vibrant colors",
    "lowpoly": "low-poly 3D model, flat shaded facets, minimal palette",
    "clay": "clay sculpture, matte single-color material, soft studio light",
    "figurine": "collectible vinyl figurine, glossy paint, toy photography",
}

_3D_SUFFIX = (
    "single object, centered, entire object fully visible with margin around it, "
    "three-quarter view, plain pure white background, soft even studio lighting, "
    "no shadow on background, no text, no watermark, no other objects"
)

NEGATIVE = (
    "multiple objects, cropped, cut off, busy background, scenery, text, watermark, "
    "logo, blurry, low quality, frame, border"
)


def build_prompt(idea: str, style: str = "realistic", optimize_for_3d: bool = True) -> str:
    idea = " ".join(idea.split()).strip().rstrip(".")
    if not idea:
        raise ValueError("Describe your idea first.")
    if not optimize_for_3d:
        return idea
    style_text = STYLES.get(style, STYLES["realistic"])
    return f"{idea}. {style_text}. {_3D_SUFFIX}"
