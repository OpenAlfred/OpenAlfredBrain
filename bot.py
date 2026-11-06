"""Alfred im Browser: Voxtral Realtime -> Mistral Small -> Voxtral TTS.

Start:  python bot.py
Dann im Browser http://localhost:7860 öffnen und auf Connect klicken.

Für den Dauerbetrieb im Raum mit Wachwort siehe src/alfred/room.py.
"""

import sys

from dotenv import load_dotenv
from loguru import logger
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.runner import WorkerRunner
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.transports.base_transport import BaseTransport, TransportParams

from alfred import logbuch
from alfred.config import load_settings
from alfred.persona import load_persona
from alfred.pipeline import build_voice_loop, require_api_key

load_dotenv(override=True)


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    settings = load_settings()
    persona = load_persona()
    sitzung = await build_voice_loop(transport, settings, persona)
    worker, context = sitzung.worker, sitzung.context

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)

    @worker.rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi):
        if settings.gespraech.begruessung:
            context.add_message(
                {"role": "developer", "content": f"Begrüße {persona.nutzer} in einem kurzen Satz."}
            )
            await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Verbindung getrennt")
        await runner.cancel()

    try:
        await runner.run()
    finally:
        await sitzung.abschliessen()


async def bot(runner_args: RunnerArguments):
    """Einstiegspunkt für Pipecats Runner."""
    # Erst hier: Pipecats main() hat vorher alle Logger-Senken ersetzt. Wirkt nur beim ersten Mal.
    logbuch.einrichten("browser")
    transport_params = {
        "webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True),
    }
    transport = await create_transport(runner_args, transport_params)
    try:
        await run_bot(transport, runner_args)
    except Exception:
        # Sonst bleibt die WebRTC-Verbindung offen, und der Browser wartet stumm auf einen
        # Bot, der nie startet; der Traceback ginge im Pipecat-Log unter.
        logger.exception("Alfred konnte nicht starten, Verbindung wird getrennt")
        verbindung = getattr(runner_args, "webrtc_connection", None)
        if verbindung:
            await verbindung.disconnect()
        raise


if __name__ == "__main__":
    from pipecat.runner.run import main

    # Pipecats Start-Banner enthält Unicode-Rahmenzeichen. Unter Windows stürzt der Start
    # damit ab, sobald die Ausgabe in eine Datei oder Pipe umgeleitet wird (cp1252).
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    require_api_key()
    main()
