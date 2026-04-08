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

    def read_chat(self):
        """Return a list of new chat messages since last call.

        Each message is a dict: {"id": str, "sender": str, "text": str}.
        Returns an empty list if no new messages.
        """
        raise NotImplementedError

    def get_participant_count(self):
        """Return the number of participants currently in the meeting.

        Returns 0 if the count cannot be determined.
        """
        return 0

    def leave(self):
        raise NotImplementedError
