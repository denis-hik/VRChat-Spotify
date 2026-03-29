import traceback

try:
    from os import system
    import asyncio

    import comtypes
    import pywintypes
    import winsdk.windows.media.control as wmc
    from comtypes import CoInitialize, CoUninitialize
    from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
    from pycaw.constants import CLSID_MMDeviceEnumerator, DEVICE_STATE, EDataFlow
    from pycaw.pycaw import AudioSession, IAudioSessionControl2, IAudioSessionManager2, ISimpleAudioVolume
    from pythonosc.dispatcher import Dispatcher
    from pythonosc.osc_server import AsyncIOOSCUDPServer
    from pythonosc.udp_client import SimpleUDPClient
    from win32api import keybd_event
    from win32con import KEYEVENTF_EXTENDEDKEY, VK_MEDIA_NEXT_TRACK, VK_MEDIA_PLAY_PAUSE, VK_MEDIA_PREV_TRACK

    def splash():
        asci = """
      _    ______  ________          __  _____             __  _ ____      ______            __             __
     | |  / / __ \/ ____/ /_  ____ _/ /_/ ___/____  ____  / /_(_) __/_  __/ ____/___  ____  / /__________  / /__  _____
     | | / / /_/ / /   / __ \/ __ `/ __/\__ \/ __ \/ __ \/ __/ / /_/ / / / /   / __ \/ __ \/ __/ ___/ __ \/ / _ \/ ___/
     | |/ / _, _/ /___/ / / / /_/ / /_ ___/ / /_/ / /_/ / /_/ / __/ /_/ / /___/ /_/ / / / / /_/ /  / /_/ / /  __/ /
     |___/_/ |_|\____/_/ /_/\__,_/\__//____/ .___/\____/\__/_/_/  \__, /\____/\____/_/ /_/\__/_/   \____/_/\___/_/
                                          /_/                    /____/
                                                                                                          by Jakhaxz
                                                                                                          ed Denis Hik
                                                                                                          """
        print(asci)

    chatboxState = 0
    muteSelf = 0
    current_session = None

    MEDIA_APP_HINTS = {
        "Spotify": ("spotify",),
        "Chrome": ("chrome",),
        "Y Music": ("yandex", "yamusic", "music.yandex", "yan"),
    }

    ######## GET SPOTIFY NOW PLAYING ########

    def getMuteselfText():
        if muteSelf == 1:
            return "[mic off] "
        return ""

    def get_media_program(media_session):
        app_id = (getattr(media_session, "source_app_user_model_id", "") or "").lower()
        for program, hints in MEDIA_APP_HINTS.items():
            if any(hint in app_id for hint in hints):
                return program
        return None

    def get_media_session_candidates(session_manager):
        candidates = []
        main_session = session_manager.get_current_session()
        if main_session:
            candidates.append(main_session)

        sessions = session_manager.get_sessions()
        if sessions:
            for index in range(sessions.size):
                candidate = sessions.get_at(index)
                if candidate is not None:
                    candidates.append(candidate)
        return candidates

    def pick_media_session(session_manager):
        preferred_sessions = []
        fallback_sessions = []
        seen_ids = set()

        for media_session in get_media_session_candidates(session_manager):
            app_id = getattr(media_session, "source_app_user_model_id", None)
            if app_id in seen_ids:
                continue
            seen_ids.add(app_id)

            program = get_media_program(media_session)
            if not program:
                continue

            playback_info = media_session.get_playback_info()
            if playback_info and playback_info.playback_status == wmc.GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING:
                preferred_sessions.append((media_session, program))
            else:
                fallback_sessions.append((media_session, program))

        if preferred_sessions:
            return preferred_sessions[0]
        if fallback_sessions:
            return fallback_sessions[0]
        return None, None

    async def get_media_info():
        session_manager = await wmc.GlobalSystemMediaTransportControlsSessionManager.request_async()

        global current_session
        current_session, program = pick_media_session(session_manager)
        if not current_session:
            return None

        info = await current_session.try_get_media_properties_async()
        info_dict = {song_attr: info.__getattribute__(song_attr) for song_attr in dir(info) if song_attr[0] != "_"}
        info_dict["genres"] = list(info_dict["genres"])
        info_dict["program"] = program
        return info_dict

    def mediaIs(state):
        if current_session is None:
            return False

        playback_info = current_session.get_playback_info()
        if playback_info is None:
            return False

        return int(wmc.GlobalSystemMediaTransportControlsSessionPlaybackStatus[state]) == playback_info.playback_status

    ######## MEDIA CONTROLS START ########

    def pauseTrack(unused_addr, arg):
        if arg:
            keybd_event(VK_MEDIA_PLAY_PAUSE, 0, KEYEVENTF_EXTENDEDKEY, 0)
            print("Detected Play/Pause")

    def nextTrack(unused_addr, arg):
        if arg:
            keybd_event(VK_MEDIA_NEXT_TRACK, 0, KEYEVENTF_EXTENDEDKEY, 0)
            print("Detected Next Track")

    def previousTrack(unused_addr, arg):
        if arg:
            keybd_event(VK_MEDIA_PREV_TRACK, 0, KEYEVENTF_EXTENDEDKEY, 0)
            print("Detected Previous")

    def get_all_audio_sessions():
        audio_sessions = []
        enumerator = comtypes.CoCreateInstance(
            CLSID_MMDeviceEnumerator,
            IMMDeviceEnumerator,
            comtypes.CLSCTX_INPROC_SERVER,
        )
        devices = enumerator.EnumAudioEndpoints(EDataFlow.eRender.value, DEVICE_STATE.ACTIVE.value)
        device_count = devices.GetCount()

        for device_index in range(device_count):
            device = devices.Item(device_index)
            if device is None:
                continue

            manager_interface = device.Activate(
                IAudioSessionManager2._iid_,
                comtypes.CLSCTX_ALL,
                None,
            )
            if manager_interface is None:
                continue

            session_manager = manager_interface.QueryInterface(IAudioSessionManager2)
            session_enumerator = session_manager.GetSessionEnumerator()
            session_count = session_enumerator.GetCount()

            for session_index in range(session_count):
                control = session_enumerator.GetSession(session_index)
                if control is None:
                    continue

                control2 = control.QueryInterface(IAudioSessionControl2)
                if control2 is not None:
                    audio_sessions.append(AudioSession(control2))

        return audio_sessions

    def volSlider(unused_addr, arg):
        CoInitialize()
        try:
            for session in get_all_audio_sessions():
                if session.Process and session.Process.name().lower().startswith("spotify"):
                    volume = session._ctl.QueryInterface(ISimpleAudioVolume)
                    print("Spotify volume: %s" % str(round(volume.GetMasterVolume(), 2)))
                    volume.SetMasterVolume(arg, None)
        finally:
            CoUninitialize()

    def chatBox(unused_addr, arg):
        global chatboxState
        if arg != chatboxState:
            if arg == 0:
                chatboxState = 0
                clearChat()
                print("Now Playing Disabled")
            else:
                chatboxState = 1
                print("Now Playing Enabled")

    def infoMic(unused_addr, arg):
        print("muted!" + str(arg))
        global muteSelf
        if arg == 0:
            muteSelf = 0
        else:
            muteSelf = 1

    ######## MEDIA CONTROLS END ########

    def clear():
        system("cls")

    def filter_handler(address, *args):
        print(f"{address}: {args}")

    dispatcher = Dispatcher()
    dispatcher.map("/avatar/parameters/pause-play", pauseTrack)
    dispatcher.map("/avatar/parameters/next-track", nextTrack)
    dispatcher.map("/avatar/parameters/previous-track", previousTrack)
    dispatcher.map("/avatar/parameters/vol-slider", volSlider)
    dispatcher.map("/avatar/parameters/now-playing", chatBox)
    dispatcher.map("/avatar/parameters/isMuteSelf", infoMic)

    serverip = "127.0.0.1"
    serverport = 9100

    def sendChat(nowPlaying, program):
        if chatboxState == 1:
            client.send_message("/chatbox/input", [getMuteselfText() + "[" + program + "] ~ " + nowPlaying, True])
        else:
            client.send_message("/chatbox/input", ["" + getMuteselfText(), True])
        client.send_message("/avatar/parameters/isPlay", True)
        if program == "Y Music":
            client.send_message("/avatar/parameters/isPlayY", True)
        else:
            client.send_message("/avatar/parameters/isPlayY", False)
        client.send_message("/avatar/parameters/now-playing", chatboxState == 1)

    def clearChat():
        client.send_message("/chatbox/input", ["" + getMuteselfText(), True])
        client.send_message("/avatar/parameters/isPlay", False)
        client.send_message("/avatar/parameters/isPlayY", False)
        client.send_message("/avatar/parameters/now-playing", chatboxState == 1)

    async def loop():
        global current_media_info
        while True:
            if mediaIs("PLAYING") is True:
                clear()
                splash()
                current_media_info = await get_media_info()
                if current_media_info is not None:
                    title = current_media_info.get("title")
                    artist = current_media_info.get("artist")
                    program = current_media_info.get("program")
                    nowPlaying = "%s - %s" % (title, artist)
                    print("[" + program + "] Now Playing: %s" % nowPlaying)
                    sendChat(nowPlaying, program)

                await asyncio.sleep(2)
            else:
                clearChat()
                clear()
                splash()
                print("Nothing is playing")
                client.send_message("/chatbox/input", ["" + getMuteselfText(), True])
                try:
                    current_media_info = await get_media_info()
                except Exception:
                    print("Searching for an open Spotify.exe process...")
                    await asyncio.sleep(5)
                    continue
                await asyncio.sleep(2)

    async def init_main():
        global client
        global current_media_info
        server = AsyncIOOSCUDPServer((serverip, serverport), dispatcher, asyncio.get_event_loop())
        transport, protocol = await server.create_serve_endpoint()

        clientip = "127.0.0.1"
        clientport = 9000

        client = SimpleUDPClient(clientip, clientport)

        while True:
            try:
                current_media_info = await get_media_info()
                break
            except Exception:
                print("Searching for an open Spotify.exe process...")
                await asyncio.sleep(5)

        splash()

        await loop()

        transport.close()

    asyncio.run(init_main())

except Exception as e:
    print(e)
    f = open("error.log", "w", encoding="UTF-8")
    f.write(str(e))
    f.close()
