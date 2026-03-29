class MeetingConnector:
    def __init__(self):
        self.join_status = None  # Set by join(); see session.JoinStatus

    def join(self, meeting_url):
        raise NotImplementedError

    def get_audio_stream(self):
        raise NotImplementedError

    def send_audio(self, audio_data):
        raise NotImplementedError

    def send_chat(self, message):
        raise NotImplementedError

    def leave(self):
        raise NotImplementedError
