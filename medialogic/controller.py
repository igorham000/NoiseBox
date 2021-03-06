"""The objects in this module control the base "vlc" library.

In particular, it acts as an abstraction layer so clients don't need to worry about all
the finicky hacky bits required to deal with the vlc library directly.

This is also a good layer to reimplement if you want to provide multiple audio backends (e.g. Spotipy).
"""

from typing import List, Optional, Dict

import vlc  # type: ignore
from vlc import AudioOutputDevice

from medialogic import oracles
from medialogic.media_library import MediaLibrary
from medialogic.player import Player

FREED_ERROR_STRING = "Invalid object - already freed."
DEFAULT_ENCODING = 'utf-8'


class UseAfterFreeException(Exception):
    pass


class AudioDevice(object):
    """Represents a single audio output device.

    We use this so we can have a handle for potential audio outputs provided by Windows or whatever.
    """

    def __init__(self, audio_output_device: AudioOutputDevice):
        self.contents = audio_output_device.contents

    def __str__(self):
        return str(self.contents.description, DEFAULT_ENCODING)

    def __repr__(self):
        return str(self)


class AudioDevices(object):
    """Represents a list of Audio Devices and interfaces with VLC to manage said list.

    This object requires active memory management. VLC uses pointers and such to manage audio device objects, and will
    leak memory and potentially other badness if you don't proactively call "free" once this object is no longer in
    use. (Yay c libraries adapted to python.)

    This class assumes you're using it from the command line, and so a lot of important information is available
    primarily through str(self) and repr(self). If you want to use this class programmatically, I suggest implementing
    new methods to access the user_device_map and device_name_map **and then updating this comment**.
    """

    def __init__(self, audio_device_enum: AudioOutputDevice):
        """Initialization for the Audio Devices method.

        :param audio_device_enum: The first audio device returned by VLC, used to build up the full list of audio
          devices.
        """
        self.device_list_ptr = audio_device_enum
        device_list = AudioDevices._build_devices(audio_device_enum)
        # The user uses this map to say "I want to play on device 3" instead of "I want to play on device <hex garbage>"
        self.user_device_map = {idx: device for idx, device in enumerate(device_list)}

        # This maps system device names back to real devices. "None" equates to "default"
        self.device_name_map: Dict[Optional[str], Optional[AudioDevice]] = {None: self.user_device_map.get(0, None)}
        for device in device_list:
            self.device_name_map[str(device.contents.device, DEFAULT_ENCODING)] = device

        self._valid = True

    def valid(self):
        """If this returns false, the object has been freed."""
        return self._valid

    def free(self):
        """Call this once you are done with the audio device list.

        This might happen if you are, for instance, refreshing the list of audio devices with a new list.
        """
        if self.valid():
            vlc.libvlc_audio_output_device_list_release(self.device_list_ptr)
            self._valid = False

    def __str__(self):
        if not self.valid():
            return FREED_ERROR_STRING
        output = ("\t%s: %s" % (entry[0], str(entry[1])) for entry in self.user_device_map.items())
        return "\n%s" % ('\n'.join(output),)

    def __repr__(self):
        if not self.valid():
            return "<Invalid object - already freed>"
        return repr(self.user_device_map)

    def device_for_index(self, index: int) -> AudioDevice:
        """Returns an AudioDevice object based on the index.

        You can find the index of an audio device using the str() function's output, or if it hasn't already been done,
        by implementing a function that returns the list of AudioDevices mapped to numbers directly.
        """
        if not self.valid():
            raise UseAfterFreeException()
        return self.user_device_map[index]

    def device_from_device_name(self, device_name: str):
        """Returns an audio device based on the name, as indicated by str(self).

        This class was really mostly intended to be used pretty close to the command line...
        """
        return self.device_name_map[device_name]

    @staticmethod
    def _build_devices(audio_output_device: AudioOutputDevice) -> List[AudioDevice]:
        """Build a list of audio devices as passed in through VLC

        VLC uses a linked list to pass around Audio Devices. As a result, we need to build the actual list by traversing
        the linked list and turning it into a python list so there's a bit more sane way to refer to devices.
        """
        cur = audio_output_device
        output = []
        while cur:
            output.append(AudioDevice(cur))
            cur = cur.contents.next
        return output


class Controller(object):
    """
    This object abstracts the VLC player object into something that manages state over long periods of time.

    In addition to passing the basic "next song", "play", "pause" functions through, it passes through a variety of
    "queue", "interrupt", "play xyz" and so on functions to help the player ensure whatever they want played is
    scheduled predictably tens of minutes or more into the future.

    It does this using a heirarchy of Oracles which get passed into the player. A the top is the "interrupt" oracle,
    which lets the caller ensure that whatever they want to play *rite nao* is played *rite nao*.

    Next is the "switch oracle", which holds the current "chain oracle" being worked through. The "switch oracle" is set
    as the "default" oracle for the above "interrupt oracle".

    Next, we have the above mentioned "chain oracle", which allows queueing playlists. You can also queue a playlist
    to repeat at the end.

    Finally, we have the individual "Playlist oracle"s or "Repeat oracle"s. These form the basis of what's going to
    get played.

    You can think of the above as a tree whenever we go to the next track:
      First we look to see if we need to play any "interrupt" songs, and play that and exit if there's something there
      Then we look to see what's next in the current playlist, and play it if there's something there
      Then we either restart the playlist (if it's a repeat oracle) or go to the next playlist
      And failing all of the above, we end the current set of songs.
    """

    def __init__(self, media_library: Optional[MediaLibrary] = None):
        if not media_library:
            media_library = MediaLibrary()
        self.media_library = media_library
        self.vlc_player = Player()
        self.devices = AudioDevices(vlc.libvlc_audio_output_device_enum(self.vlc_player.mp))
        self.switch_oracle = oracles.SwitchOracle()
        self.queueing_oracle = oracles.ChainOracle()
        self.interrupt_oracle = oracles.InterruptOracle(self.switch_oracle)
        self.switch_oracle.set_oracle(self.queueing_oracle)
        self.vlc_player.play_oracle(self.interrupt_oracle)

    def play(self, song_alias: str):
        """
        Clear out all existing songs from the oracle list and play the new set of songs indicated by the alias.

        :param song_alias: Refers to an alias in the media library to interrupt literally everything and play now.
        """
        songs = self.media_library.get(song_alias)
        self.queueing_oracle = oracles.ChainOracle()
        self.switch_oracle.set_oracle(self.queueing_oracle)
        self.queueing_oracle.add(oracles.PlaylistOracle(songs))
        self.interrupt_oracle.clear_interrupt()
        self.vlc_player.next_song()

    def toggle_pause(self):
        """Toggle pausing playback. Returns true if now paused, or false otherwise."""
        return self.vlc_player.toggle_pause()

    def set_pause(self, value: bool):
        return self.vlc_player.set_pause(value)

    def paused(self) -> bool:
        """Return true if currently paused, but false in all other cases (incl. stopped)."""
        return self.vlc_player.paused()

    def playing(self) -> bool:
        """Returns true if currently playing or about to play (e.g. buffering), and false in all other cases."""
        return self.vlc_player.playing()

    def stop(self):
        """Stops current playback."""
        self.vlc_player.stop()

    def queue(self, alias: str):
        """
        Tacks a set of songs onto the end of the existing playlist chain.
        :param alias:
        :return:
        """
        songs = self.media_library.get(alias)
        self.queueing_oracle.add(oracles.PlaylistOracle(songs))

    def queue_repeat(self, alias: str, times=None):
        """
        Add a repeating playlist to the queue.

        :param alias: The name of the thing to play in the media library
        :param times: The number of times to repeat. If None, repeat forever.
        """
        songs = self.media_library.get(alias)
        self.queueing_oracle.add(oracles.RepeatingOracle(songs, times))

    def list_devices(self) -> str:
        """Lists the current audio devices as a string."""
        self.devices.free()
        self.devices = AudioDevices(vlc.libvlc_audio_output_device_enum(self.vlc_player.mp))
        return str(self.devices)

    def set_device(self, device_idx):
        """Sets the current device using an int index based on 'list_devices' string."""
        self.vlc_player.set_device(self.devices.device_for_index(device_idx).contents.device)

    def get_device(self):
        """Gets a string that describes the audio output devices."""
        return str(self.devices.device_from_device_name(self.vlc_player.mp.audio_output_device_get()))
