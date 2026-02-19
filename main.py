import os
import json
import random
import base64
import subprocess
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
from openai import OpenAI
from openai import RateLimitError, BadRequestError

# ----------------------------
# Setup
# ----------------------------
ROOT = Path(__file__).parent
ASSETS = ROOT / "assets"
INTROS_DIR = ASSETS / "intros"
OUTRO_VIDEO = ASSETS / "outro.mp4"

OUTPUT_DIR = ROOT / "output"
TEMP_DIR = ROOT / "temp"
OUTPUT_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)

W, H = 1080, 1920
FPS = 30
INTRO_SECS = 4.0
IMAGE_SECS = 3.4
OUTRO_SECS = 4.0
XFADE_SECS = 0.35

FONT_FILE = ASSETS / "font.ttf"

# ----------------------------
# Overlay styling toggles
# ----------------------------
TITLE_Y = 0.35          # 0.35 = a bit above center (0.50 would be dead center)
TITLE_BOX = True        # set False to remove title background box

NUMBER_BOX = True       # set False to remove number black box
OPTION_BOX = True       # set False to remove option text black box

BOX_OPACITY = 0.45      # black@0.45
NUMBER_BOX_PAD = 20
OPTION_BOX_PAD = 18
TITLE_BOX_PAD = 18


def run(cmd: list[str]):
    subprocess.run(cmd, check=True)


def pick_random_intro() -> Path:
    vids = list(INTROS_DIR.glob("*.mp4"))
    if not vids:
        raise FileNotFoundError(f"No intro videos found in: {INTROS_DIR}")
    return random.choice(vids)


def ffmpeg_escape_path(p: Path) -> str:
    # drawtext uses ':' as separator; Windows 'C:' must be escaped as 'C\:'.
    s = str(p.resolve()).replace("\\", "/")
    if len(s) >= 2 and s[1] == ":":
        s = s[0] + "\\:" + s[2:]
    return s


def ffmpeg_quote_text(s: str) -> str:
    # Wrap in single quotes; escape backslash and single quote.
    s = s.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{s}'"


def scale_crop_filter() -> str:
    # This matches your LOCAL TEST (works reliably):
    return f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"


# ----------------------------
# OpenAI helpers
# ----------------------------
def make_client() -> OpenAI:
    load_dotenv()  # make sure .env is loaded first
    key_loaded = bool(os.getenv("OPENAI_API_KEY"))
    print("API KEY LOADED:", key_loaded)
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def llm_text(client: OpenAI, prompt: str, model: str = "gpt-5-mini") -> str:
    resp = client.responses.create(model=model, input=prompt, )
    return (resp.output_text or "").strip()


def generate_options_from_title(client: OpenAI, title: str) -> list[str]:
    prompt = f"""
You are creating 5 visually distinct options for a vertical short "Pick 1-5" video.

User TITLE / topic:
{title}

Return STRICT JSON only (no extra text):
{{
  "options": ["opt1","opt2","opt3","opt4","opt5"]
}}

Rules:
- exactly 5 options
- each option must be 2–6 words
- fun + clearly different
- no emojis
- match the topic of the title
- keep them visually distinct so images look different
- entire object visible
- fully in frame
- no humans or human parts (face, hands, etc)
- realistic not illustrated
- avoid common or generic answers
- do not repeat typical lists
- make each option visually and conceptually distinct
- each option should feel like a different “scene”
"""
    raw = llm_text(client, prompt)

    # Guard: if the API errors or returns empty, raw could be ""
    if not raw:
        raise RuntimeError("LLM returned empty text (possible quota/billing issue).")

    # Try strict JSON parse; if it fails, show the raw output to debug quickly
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError(f"LLM did not return valid JSON. Raw output:\n{raw}")

    options = [o.strip() for o in data.get("options", [])]
    if len(options) != 5:
        raise ValueError(f"Expected exactly 5 options, got {len(options)}: {options}")
    return options


def generate_image(client: OpenAI, prompt: str, out_path: Path):
    img = client.images.generate(
        model="gpt-image-1",
        prompt=prompt,
        size="1024x1024",
    )
    b64 = img.data[0].b64_json
    out_path.write_bytes(base64.b64decode(b64))


# ----------------------------
# Video steps
# ----------------------------
def trim_scale(in_path: Path, out_path: Path, secs: float):
    run([
        "ffmpeg", "-y",
        "-i", str(in_path),
        "-t", str(secs),
        "-vf", scale_crop_filter(),
        "-r", str(FPS),
        "-pix_fmt", "yuv420p",
        str(out_path)
    ])


def overlay_title(in_path: Path, out_path: Path, title: str):
    if not FONT_FILE.exists():
        raise FileNotFoundError(f"Missing font file: {FONT_FILE}")

    fontpath = ffmpeg_escape_path(FONT_FILE)
    font = f"fontfile='{fontpath}':"

    box_part = ""
    if TITLE_BOX:
        box_part = f":box=1:boxcolor=black@{BOX_OPACITY}:boxborderw={TITLE_BOX_PAD}"
    else:
        box_part = ":box=0"

    vf = (
        f"drawtext={font}"
        f"text={ffmpeg_quote_text(title)}:"
        f"x=(w-text_w)/2:y=h*{TITLE_Y}:"
        f"fontsize=72:fontcolor=white"
        f"{box_part}"
    )

    run([
        "ffmpeg", "-y",
        "-i", str(in_path),
        "-vf", vf,
        "-pix_fmt", "yuv420p",
        str(out_path)
    ])


def make_image_clip(img_path: Path, out_path: Path):
    run([
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(img_path),
        "-t", str(IMAGE_SECS),
        "-r", str(FPS),
        "-vf", scale_crop_filter(),
        "-pix_fmt", "yuv420p",
        str(out_path)
    ])


def overlay_number_and_text(in_path: Path, out_path: Path, number: int, text: str):
    def esc_drawtext(s: str) -> str:
        # escape for ffmpeg drawtext value
        # order matters: escape backslash first
        return (
            s.replace("\\", "\\\\")
             .replace(":", "\\:")     # IMPORTANT (your bug)
             .replace("'", "\\'")     # keep since you wrap in single quotes
        )

    fontpath = ffmpeg_escape_path(FONT_FILE)
    font = f"fontfile='{fontpath}':"

    num = esc_drawtext(str(number))
    txt = esc_drawtext(text)

    vf = ",".join([
        f"drawtext={font}text='{num}':x=w*0.08:y=h*0.10:fontsize=140:fontcolor=white:"
        f"box=1:boxcolor=black@0.45:boxborderw=20",
        f"drawtext={font}text='{txt}':x=(w-text_w)/2:y=h*0.80:fontsize=70:fontcolor=white:"
        f"box=1:boxcolor=black@0.45:boxborderw=18",
    ])

    run([
        "ffmpeg", "-y",
        "-i", str(in_path),
        "-vf", vf,
        "-pix_fmt", "yuv420p",
        str(out_path)
    ])

  


def xfade_chain(clips: list[Path], out_path: Path):
    cmd = ["ffmpeg", "-y"]
    for c in clips:
        cmd += ["-i", str(c)]

    d = IMAGE_SECS
    x = XFADE_SECS

    filters = []
    last = "[0:v]"
    for i in range(1, len(clips)):
        offset = (d - x) * i
        out = f"[v{i}]"
        filters.append(
            f"{last}[{i}:v]xfade=transition=fade:duration={x}:offset={offset}{out}"
        )
        last = out

    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", last,
        "-r", str(FPS),
        "-pix_fmt", "yuv420p",
        str(out_path)
    ]
    run(cmd)


def concat_three(intro: Path, montage: Path, outro: Path, out_path: Path):
    run([
        "ffmpeg", "-y",
        "-i", str(intro),
        "-i", str(montage),
        "-i", str(outro),
        "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
        "-map", "[v]",
        "-r", str(FPS),
        "-pix_fmt", "yuv420p",
        str(out_path)
    ])


def make_image_clips(client: OpenAI, options: list[str], title: str) -> list[Path]:
    clips = []
    for i, opt in enumerate(options, start=1):
        img_path = TEMP_DIR / f"img_{i}.png"
        clip_path = TEMP_DIR / f"img_{i}.mp4"
        labeled_path = TEMP_DIR / f"img_{i}_labeled.mp4"

        img_prompt = f"""
Create a photorealistic, ultra-detailed vertical image.

Scene idea (title/topic): {title}
Specific choice: {opt}

Style / realism:
- PHOTOREALISTIC (real-life photo), not illustration, not cartoon, not 3D render
- DSLR photo, 50mm lens look, shallow depth of field, natural lighting, high dynamic range
- realistic textures, realistic materials, realistic shadows

Composition:
- single clear main subject centered
- clean background, minimal clutter
- vertical framing suitable for 9:16 crop

Hard rules:
- NO text anywhere
- NO logos, NO watermarks
- NO signs, NO “for sale” boards, NO labels, NO captions
"""


        generate_image(client, img_prompt, img_path)

        make_image_clip(img_path, clip_path)
        overlay_number_and_text(clip_path, labeled_path, i, opt)
        clips.append(labeled_path)

    return clips


def main():
    if not OUTRO_VIDEO.exists():
        raise FileNotFoundError(f"Missing outro video: {OUTRO_VIDEO}")
    if not FONT_FILE.exists():
        raise FileNotFoundError(f"Missing font file: {FONT_FILE}")

    client = make_client()

    title = input("Enter TITLE (e.g. Pick Your Breakfast): ").strip()
    if not title:
        print("No title provided. Exiting.")
        return

    intro_src = pick_random_intro()
    print("Picked intro:", intro_src.name)

    try:
        options = generate_options_from_title(client, title)
    except (RateLimitError, BadRequestError) as e:
        print("\nOpenAI error (billing/quota likely):")
        print(e)
        return

    print("Title:", title)
    print("Options:", options)

    # Intro/outro prep
    intro_trim = TEMP_DIR / "intro_trim.mp4"
    intro_titled = TEMP_DIR / "intro_titled.mp4"
    outro_trim = TEMP_DIR / "outro_trim.mp4"

    trim_scale(intro_src, intro_trim, INTRO_SECS)
    overlay_title(intro_trim, intro_titled, title)
    trim_scale(OUTRO_VIDEO, outro_trim, OUTRO_SECS)

    # Images + montage
    try:
        labeled_clips = make_image_clips(client, options, title)
    except (RateLimitError, BadRequestError) as e:
        print("\nOpenAI error during image generation (billing/hard limit likely):")
        print(e)
        return

    montage = TEMP_DIR / "montage.mp4"
    xfade_chain(labeled_clips, montage)

    # Final
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    final_out = OUTPUT_DIR / f"final_{stamp}.mp4"
    concat_three(intro_titled, montage, outro_trim, final_out)

    print("\nDONE:", final_out)


if __name__ == "__main__":
    main()
