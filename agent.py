from dotenv import load_dotenv
from livekit import agents
from livekit.plugins import (
    openai,
    noise_cancellation,
    silero
)
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from livekit.agents.llm import ChatContext, ChatMessage, ImageContent, AudioContent
from livekit import api
import os
from services.database_handler import fetch_session_data, save_session_message
from livekit.agents import ConversationItemAddedEvent
import json
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    UserStateChangedEvent,
    WorkerOptions,
    cli,
    RoomInputOptions,
    AutoSubscribe,
    RoomOutputOptions
)
import datetime

from livekit.plugins.openai import STT
from livekit import rtc
import asyncio
load_dotenv()
AGENT_IDENTITY = "assistant"
class Assistant(Agent):
    def __init__(self, resume: str, job_description: str, lang: str) -> None:
        super().__init__(
            instructions=(

                f"""
                
                You are a helpful interview assistant and speak in {lang}.
                Use Resume and Job description to ask interview question.
                Total ask 10 questions,Ask one by one and wait for user to respond properly and then ask next question.
                If user anser all the 10 questions then end the interview.
                Start the interview by greeting the participant warmly.

                Ask questions specifically related to the provided job description.

                Analyze the user's voice—if you detect pauses, fumbling, or utterances like “uhh” or “hmm,” politely ask if they need a moment to gather their thoughts.

                Before moving on to the next question, ask the user if they’ve finished their answer or would like to add anything more.

                If the user remains silent or gives an unrelated response for 20 seconds, prompt them gently by asking if they would like to proceed to the next question.

                If the total number of questions is 10 and the user has answered only 5, and only 2 minutes remain, inform them that time is running short. Let them know how many questions are left and share their difficulty level.

                When half of the allotted time has passed, notify the user that they are at the halfway mark, with half the time remaining.

                Give the user a reminder when only 5 minutes are left in the interview.
                
                When all the questions asked and answered by user. Then end the interview with wishing him luck and say exactly 'Have a great day and Goodbye!!' 
                Resume:
                {resume}

                Job Description:
                {job_description}
                """
            )
        )



async def entrypoint(ctx: agents.JobContext):
    resume, job_description, lang = fetch_session_data("1cb46942-8e5b-4f1d-8576-e364ca609fe6")

    try:
        req = api.RoomCompositeEgressRequest(
            room_name=ctx.room.name,
            preset=api.EncodingOptionsPreset.H264_720P_30,  
            audio_only=False,
            file_outputs=[api.EncodedFileOutput(
                file_type=api.EncodedFileType.MP4,  
                filepath=f"{ctx.room.name}/recording.mp4", 
                s3=api.S3Upload(
                    bucket=os.getenv('S3_BUCKET'),
                    region=os.getenv('S3_REGION'),
                    access_key=os.getenv('S3_ACCESS_KEY'),
                    secret=os.getenv('S3_SECRET_KEY'),
                    force_path_style=True,
                ),
            )],
        )

        lkapi = api.LiveKitAPI()
        res = await lkapi.egress.list_egress(
            api.ListEgressRequest()
        )
        res = await lkapi.egress.start_room_composite_egress(req)

        await lkapi.aclose()
    except Exception as e:
        print("Cound not start recording", e)

    await ctx.connect(auto_subscribe=AutoSubscribe.SUBSCRIBE_ALL)
    await ctx.wait_for_participant()

    async def _handle_text_stream(reader, participant_info):
        try:
            info = reader.info
            track_id = info.attributes.get("lk.transcribed_track_id")
            # incremental chunks (works for interim + final segments)
            async for chunk in reader:
                # attributes often strings — treat conservatively
                is_final_attr = info.attributes.get("lk.transcription_final")
                is_final = is_final_attr in (True, "true", "1")

                payload = {
                    "text": chunk,
                    "speaker": participant_info.identity,
                    "final": bool(is_final),
                    "track_id": track_id,
                    "stream_id": info.id,
                }

                # send to frontend as a reliable data packet on topic "transcription"
                await ctx.room.local_participant.publish_data(
                    json.dumps(payload).encode("utf-8"),
                    kind=rtc.DataPacketKind.RELIABLE_ORDERED,
                    topic="transcription",
                )

        except Exception as e:
            print("transcription handler error:", e)

    session = AgentSession(
        stt = openai.STT.with_azure(
            model="gpt-4o-transcribe",
        ),
        
        llm=openai.LLM.with_azure(
            azure_deployment=os.getenv("AZURE_DEPLOYMENT"),
            azure_endpoint=os.getenv("AZURE_OPENAI_TEXT_ENDPOINT"),
            api_key=os.getenv("AZURE_OPENAI_API_KEY"), 
            api_version=os.getenv("OPENAI_API_VERSION"),
        ),
        tts=openai.TTS.with_azure(
            model="gpt-4o-mini-tts",
            voice="coral",
        ),
        vad=silero.VAD.load(),
        turn_detection=MultilingualModel(),
        use_tts_aligned_transcript=True,
    )

    
    @session.on("conversation_item_added")
    def on_conversation_item_added(event: ConversationItemAddedEvent):
        for content in event.item.content:
            if isinstance(content, str):
                # save_session_message(role=event.item.role, message=content, session_uuid=ctx.room.name)
                save_session_message(role=event.item.role, message=content, session_uuid="cb46942-8e5b-4f1d-8576-e364ca609fe6")
            # elif isinstance(content, AudioContent):
            #     # frame is a list[rtc.AudioFrame]
            #     print(f" - audio: {content.frame}, transcript: {content.transcript}")

            
    inactivity_task: asyncio.Task | None = None

    async def user_presence_task():
        for _ in range(3):
            await session.generate_reply(
                instructions=(
                    "The user has been inactive. Politely check if the user is still present."
                )
            )
            await asyncio.sleep(10)

        # await asyncio.shield(session.aclose())
        # ctx.delete_room()

    @session.on("user_state_changed")
    def _user_state_changed(ev: UserStateChangedEvent):
        nonlocal inactivity_task
        if ev.new_state == "away":
            inactivity_task = asyncio.create_task(user_presence_task())
            return

        # ev.new_state: listening, speaking, ..
        if inactivity_task is not None:
            inactivity_task.cancel()
    
    @session.on("user_input_transcribed")
    def on_transcript(transcript):
        if transcript.is_final:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open("user_speech_log.txt", "a") as f:
                f.write(f"[{timestamp}] {transcript.transcript}\n") 

    try:
        await session.start(
            room=ctx.room,
            agent=Assistant(resume=resume, job_description=job_description, lang=lang),
            room_input_options=RoomInputOptions(
                close_on_disconnect=False,
                noise_cancellation=noise_cancellation.BVC(),
            ),
            room_output_options=RoomOutputOptions(transcription_enabled=True,  sync_transcription=False),
        )

        await session.generate_reply(
            instructions="Greet the user with name and start interview"
        ) 
    except Exception as e:
        print("Error during session:", e)

if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
