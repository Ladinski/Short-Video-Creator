import random
import subprocess
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent
ASSETS = ROOT / "assets"
INTROS_DIR = ASSETS / "intros"
OUTRO_VIDEO = ASSETS / "outro.mp4"
TEST_IMAGES_DIR = ASSETS / "test_images"

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

FONT_FILE = ASSETS / "font.ttf"  # optional

# ----- EDIT THESE FOR TESTING -----
TITLE = "Pick Your Breakfast now! Do it!s"
OPTIONS = [
    "Pancakes & Syrup",
    "Eggs & Bacon",
    "Cereal Mountain",
    "Toast + Avocado",
    "Waffles Supreme",
]
# ----------------------------------

def run(cmd: list[str]):
    subprocess.run(cmd, check=True)


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


def pick_random_intro() -> Path:
    vids = list(INTROS_DIR.glob("*.mp4"))
    if not vids:
        raise FileNotFoundError(f"No intro videos found in: {INTROS_DIR}")
    return random.choice(vids)


def trim_scale(in_path: Path, out_path: Path, secs: float):
    run([
        "ffmpeg", "-y",
        "-i", str(in_path),
        "-t", str(secs),
        "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}",
        "-r", str(FPS),
        "-pix_fmt", "yuv420p",
        str(out_path)
    ])


def ffmpeg_escape_path(p: Path) -> str:
    # drawtext uses ':' as separator; Windows 'C:' must be escaped as 'C\:'
    s = str(p.resolve()).replace("\\", "/")
    if len(s) >= 2 and s[1] == ":":
        s = s[0] + "\\:" + s[2:]
    return s


def overlay_title(in_path: Path, out_path: Path, title: str):
    def q(s: str) -> str:
        s = s.replace("\\", "\\\\").replace("'", "\\'")
        return f"'{s}'"

    fontpath = ffmpeg_escape_path(FONT_FILE)
    font = f"fontfile='{fontpath}':"

    vf = (
        f"drawtext={font}text={q(title)}:"
        f"x=(w-text_w)/2:y=h*0.35:"
        f"fontsize=72:fontcolor=white:"
        f"box=0:boxcolor=black@0.45:boxborderw=18"
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
        "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}",
        "-pix_fmt", "yuv420p",
        str(out_path)
    ])


def overlay_number_and_text(in_path: Path, out_path: Path, number: int, text: str):
    def q(s: str) -> str:
        s = s.replace("\\", "\\\\").replace("'", "\\'")
        return f"'{s}'"

    fontpath = ffmpeg_escape_path(FONT_FILE)
    font = f"fontfile='{fontpath}':"

    vf = ",".join([
        f"drawtext={font}text={q(str(number))}:x=w*0.08:y=h*0.10:fontsize=140:fontcolor=white:"
        f"box=0:boxcolor=black@0.45:boxborderw=20",
        f"drawtext={font}text={q(text)}:x=(w-text_w)/2:y=h*0.80:fontsize=70:fontcolor=white:"
        f"box=0:boxcolor=black@0.45:boxborderw=18",
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


def main():
    imgs = [TEST_IMAGES_DIR / f"{i}.png" for i in range(1, 6)]
    missing = [p for p in imgs if not p.exists()]
    if missing:
        raise FileNotFoundError(
            "Missing test images. Put files here:\n"
            + "\n".join(str(p) for p in missing)
        )

    if not OUTRO_VIDEO.exists():
        raise FileNotFoundError(f"Missing outro video: {OUTRO_VIDEO}")

    if not FONT_FILE.exists():
        raise FileNotFoundError(f"Missing font file: {FONT_FILE}")

    intro_src = pick_random_intro()
    print("Picked intro:", intro_src.name)

    intro_trim = TEMP_DIR / "intro_trim.mp4"
    intro_titled = TEMP_DIR / "intro_titled.mp4"
    trim_scale(intro_src, intro_trim, INTRO_SECS)
    overlay_title(intro_trim, intro_titled, TITLE)

    outro_trim = TEMP_DIR / "outro_trim.mp4"
    trim_scale(OUTRO_VIDEO, outro_trim, OUTRO_SECS)

    labeled_clips = []
    for i in range(1, 6):
        clip = TEMP_DIR / f"img_{i}.mp4"
        labeled = TEMP_DIR / f"img_{i}_labeled.mp4"
        make_image_clip(imgs[i-1], clip)
        overlay_number_and_text(clip, labeled, i, OPTIONS[i-1])
        labeled_clips.append(labeled)

    montage = TEMP_DIR / "montage.mp4"
    xfade_chain(labeled_clips, montage)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    final_out = OUTPUT_DIR / f"LOCALTEST_final_{stamp}.mp4"
    concat_three(intro_titled, montage, outro_trim, final_out)

    print("\nDONE:", final_out)


if __name__ == "__main__":
    main()
