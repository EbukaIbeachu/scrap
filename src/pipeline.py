"""Stage 9: run every stage in order."""
from __future__ import annotations

from .collector import run_collect
from .context import Ctx
from .download import offer_download
from .images import run_images
from .images.people import approve_people, run_people
from .script import run_script
from .selection import select_stories
from .utils import UserError, log
from .video.assembler import run_assemble
from .video.export import run_export
from .voice import run_voice


def run_all(ctx: Ctx, skip_collect: bool = False, skip_images: bool = False, yes: bool = False,
            download_prompt: bool = True, approve_prompt: bool = True) -> dict:
    """collect -> images -> people -> script -> voice -> assemble -> export -> download question."""
    if ctx.dry_run:
        log.info("DRY RUN: no Claude calls, silent placeholder voice. The result is for testing only.")

    steps = [
        ("Collecting stories", None if skip_collect else lambda: run_collect(ctx)),
        ("Selecting stories", lambda: select_stories(ctx)),
        ("Finding images", None if skip_images else lambda: run_images(ctx)),
        ("Finding public-figure photos", lambda: run_people(ctx)),
    ]
    for i, (label, fn) in enumerate(steps, start=1):
        if fn is None:
            log.info("[%d/9] %s: skipped", i, label)
            continue
        log.info("[%d/9] %s", i, label)
        fn()
    if not select_stories(ctx):
        raise UserError("No stories were found, so there is nothing to build. Check your feeds in config.yaml.")

    if approve_prompt:
        approve_people(ctx)

    log.info("[5/9] Writing script")
    run_script(ctx)
    log.info("[6/9] Recording voiceover")
    run_voice(ctx)
    log.info("[7/9] Assembling video")
    run_assemble(ctx)
    log.info("[8/9] Exporting for WhatsApp")
    manifest = run_export(ctx)
    log.info("[9/9] Download")
    offer_download(ctx, manifest, yes=yes, prompt=download_prompt)
    return manifest
