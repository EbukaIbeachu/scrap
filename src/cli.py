"""Command-line interface: python main.py <command>."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from .config import load_config
from .context import Ctx
from .utils import UserError, log, now_local, setup_logging

ROOT = Path(__file__).resolve().parent.parent


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=str(ROOT / "config.yaml"), help="path to config.yaml")
    common.add_argument("--date", help="YYYY-MM-DD (default: today)")
    common.add_argument("--dry-run", action="store_true", help="skip paid/AI calls: plain script + silent placeholder voice")
    common.add_argument("--force", action="store_true", help="redo work that already exists")
    common.add_argument("-v", "--verbose", action="store_true", help="show detailed logs")

    p = argparse.ArgumentParser(prog="main.py", description="Personal news highlights video tool")
    sub = p.add_subparsers(dest="command", required=True)

    def add(name: str, help_: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, parents=[common], help=help_, description=help_)

    add("collect", "stage 1: collect stories from your feeds")
    s = add("select", "show (or re-pick) today's selected stories")
    s.add_argument("--reselect", action="store_true", help="choose again from the latest stories")
    add("images", "stage 2: find images for the selected stories")
    add("people", "stage 3: find photos of public figures named in the stories")
    a = add("approve-people", "confirm that each found photo is the right person")
    a.add_argument("--all", action="store_true", help="approve every pending photo without asking (be careful)")
    add("script", "stage 4: write the highlights script with Claude")
    add("voice", "stage 5: generate the voiceover")
    vt = add("voice-test", "hear a sample sentence in a voice")
    vt.add_argument("text", nargs="?", default="Good evening. Here are today's top stories from Nigeria and around the world.")
    vt.add_argument("--provider", help="edge-tts, kokoro, piper, elevenlabs, openai, own_voice")
    vc = add("voices", "list available voices for a provider")
    vc.add_argument("--provider")
    vc.add_argument("--locale", help="for example en-NG")
    add("assemble", "stage 6: build the video")
    add("srt", "write SRT subtitle file for today's video")
    add("review", "write the review file to check before sharing")
    add("export", "stage 7: export WhatsApp-ready MP4 file(s)")
    d = add("download", "stage 8: copy a finished video to your Downloads folder")
    d.add_argument("--yes", action="store_true", help="choose the newest video without asking")
    r = add("run-all", "stage 9: run every stage in order")
    r.add_argument("--skip-images", action="store_true", help="reuse existing images")
    r.add_argument("--skip-collect", action="store_true", help="reuse stories already collected")
    r.add_argument("--yes", action="store_true", help="answer yes to the download question")
    r.add_argument("--no-download-prompt", action="store_true", help="do not ask about downloading")
    r.add_argument("--no-approve-prompt", action="store_true", help="do not ask to approve person photos")
    return p


def make_ctx(args: argparse.Namespace) -> Ctx:
    cfg_path = Path(args.config)
    root = cfg_path.resolve().parent if cfg_path.exists() else ROOT
    load_dotenv(root / ".env")
    cfg = load_config(cfg_path)
    date = args.date or now_local(cfg["general"]["timezone"]).strftime("%Y-%m-%d")
    ctx = Ctx(cfg=cfg, root=root, date=date, dry_run=args.dry_run, force=args.force)
    setup_logging(ctx.log_dir, args.verbose)
    return ctx


def dispatch(ctx: Ctx, args: argparse.Namespace) -> int:
    cmd = args.command
    if cmd == "collect":
        from .collector import run_collect

        run_collect(ctx)
    elif cmd == "select":
        from .selection import select_stories

        for i, s in enumerate(select_stories(ctx, reselect=args.reselect), start=1):
            print(f"{i}. [{s['outlet']}] {s['title']}  ({s['text_quality']})")
    elif cmd == "images":
        from .images import run_images

        run_images(ctx)
    elif cmd == "people":
        from .images.people import run_people

        run_people(ctx)
    elif cmd == "approve-people":
        from .images.people import approve_people

        approve_people(ctx, approve_all=args.all)
    elif cmd == "script":
        from .script import run_script

        path = run_script(ctx)
        print(f"Script: {path.with_suffix('.txt')}")
    elif cmd == "voice":
        from .voice import run_voice

        run_voice(ctx)
    elif cmd == "voice-test":
        from .voice import voice_test

        voice_test(ctx, args.text, args.provider)
    elif cmd == "voices":
        from .voice import list_voices

        print("\n".join(list_voices(ctx, args.provider, args.locale)) or "(no voice list for this provider)")
    elif cmd == "assemble":
        from .video.assembler import run_assemble

        print(f"Video: {run_assemble(ctx)}")
    elif cmd == "srt":
        from .video.subtitles import write_srt

        out = ctx.subdir("output") / f"news_highlights_{ctx.date}.srt"
        print(f"SRT: {write_srt(ctx, out)}")
    elif cmd == "review":
        from .review import write_review

        print(f"Review file: {write_review(ctx)}")
    elif cmd == "export":
        from .download import print_summary
        from .video.export import run_export

        print_summary(run_export(ctx))
    elif cmd == "download":
        from .download import download_command

        download_command(ctx, yes=args.yes)
    elif cmd == "run-all":
        from .pipeline import run_all

        run_all(ctx, skip_collect=args.skip_collect, skip_images=args.skip_images, yes=args.yes,
                download_prompt=not args.no_download_prompt, approve_prompt=not args.no_approve_prompt)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        ctx = make_ctx(args)
        return dispatch(ctx, args)
    except UserError as exc:
        print(f"\nProblem: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130
    except Exception as exc:  # last resort: log the traceback to the file, show a short message
        log.exception("Unexpected error")
        print(f"\nUnexpected error: {exc}\nDetails are in logs/run.log", file=sys.stderr)
        return 1
