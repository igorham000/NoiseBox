"""
This module defines objects used to manage a media library pointing at audio files in a file system.
"""

import os
from typing import List, Dict, Tuple, Any

from common.exceptions import UserException, SystemException


def _check_file_exists(song_uri):
    if os.path.isfile(song_uri):
        return
    raise NotFoundException("Could not find file '%s'" % (song_uri,))


class BadFormatException(SystemException):
    pass


VERSION_FIELD = "version"


class Song(object):
    """Represents a single song, to be fed into the Media Library."""
    VERSION = 1.0
    ALIAS_FIELD = "alias"
    URI_FIELD = "uri"
    DESCRIPTION_FIELD = "description"

    def __init__(self, alias: str, uri: str, description: str = ""):
        self.alias = alias
        _check_file_exists(uri)
        self.uri = uri
        self.description = description

    def __str__(self):
        return "Song{alias: '%s', uri: '%s', description: '%s'}" % (self.alias, self.uri, self.description)

    def __repr__(self):
        return str(self)

    @staticmethod
    def parse_v1(primitive: Dict[str, object]):
        """Used to parse the first serialization version of a song from json."""
        return Song(str(primitive["alias"]), str(primitive["uri"]), str(primitive.get("description", "")))

    def to_primitive(self) -> Dict[str, object]:
        """Transforms 'self' to a dict that can be serialized."""
        return {
            VERSION_FIELD: self.VERSION,
            Song.ALIAS_FIELD: self.alias,
            Song.URI_FIELD: self.uri,
            Song.DESCRIPTION_FIELD: self.description if self.description is not None else "",
        }

    @staticmethod
    def from_primitive(primitive: Dict[str, object]):
        """Transforms a dict (presumably read from json) into a Song object."""
        version = primitive[VERSION_FIELD]
        if not isinstance(version, float):
            raise BadFormatException("Invalid primitive '%s': bad version field. Expected float, got '%s'." % (
                primitive, type(version).__name__))

        return SONG_VERSION_PARSER[version](primitive)

    def __eq__(self, other):
        """We explicitly ignore the "description" field because it's not a 'key'."""
        if not isinstance(other, Song):
            return False
        if self.alias == other.alias and self.uri == other.uri:
            return True


# We use this dict so that, when we update the logic required to serialize a song, we can
# use the appropriate function to turn it into a song object.
SONG_VERSION_PARSER = {
    1.0: Song.parse_v1
}


class NotFoundException(UserException):
    """Thrown when a song with a given alias hasn't been registered with the library yet."""
    pass


class AlreadyExistsException(UserException):
    """Thrown when a playlist with a given name already exists."""
    pass


class IllegalArgument(UserException):
    """Thrown when an input argument is invalid."""
    pass


class MediaLibrary(object):
    """
    Holds music for future reference.

    This makes it easier to reference songs & playlists using short, human-readable phrases instead of goddamn URIs...

    Makes defensive copies on every "get" function.
     """

    # Version number. Always update when updating to_primitives.
    VERSION = 1.0
    SONGS_FIELD = "songs"
    PLAYLIST_FIELD = "playlists"

    def __init__(self):
        self.song_map = {}
        self.playlists = {}

    def to_primitive(self) -> Dict[str, object]:
        """Dump to a json-dump-able object"""
        return {
            VERSION_FIELD: self.VERSION,
            MediaLibrary.SONGS_FIELD: list(s.to_primitive() for s in self.song_map.values()),
            # In version 1.0, this is str -> List[str] mappings.
            MediaLibrary.PLAYLIST_FIELD: self.playlists,
        }

    def __str__(self):
        return "MediaLibrary%s" % (self.to_primitive(),)

    def __repr__(self):
        return self.__str__()

    def __eq__(self, other):
        if not isinstance(other, MediaLibrary):
            return False
        return self.song_map == other.song_map and self.playlists == other.playlists

    @staticmethod
    def from_primitive(primitive: Dict[str, object]):
        """Creates a MediaLibrary object from an input primitive dict."""
        version = primitive[VERSION_FIELD]
        if not isinstance(version, float):
            raise BadFormatException("Invalid primitive '%s': bad version field. Expected float, got '%s'." % (
                primitive, type(version).__name__))

        return MEDIA_LIBRARY_VERSION_PARSER[version](primitive)

    @staticmethod
    def parse_v1(primitive: Dict[str, Any]):
        """Parses a MediaLibrary object from a dict using the v1 schema."""
        ml = MediaLibrary()
        songs = [Song.from_primitive(song) for song in primitive.get(MediaLibrary.SONGS_FIELD, [])]
        ml.song_map = dict((song.alias, song) for song in songs)
        ml.playlists = primitive.get(MediaLibrary.PLAYLIST_FIELD, [])
        return ml

    def add_song(self, song: Song, expect_overwrite: bool = False) -> None:
        """Add a song from the song map. Use the stored alias as the alias in the map."""
        if not isinstance(song, Song):
            raise IllegalArgument("Expected object '%s' to be a Song object, instead got a '%s'" %
                                  (song, type(song).__name__))
        if song.alias in self.song_map and not expect_overwrite:
            raise AlreadyExistsException(
                "Song '%s' already exists in the library as '%s'" % (song, self.song_map[song.alias]))
        self.song_map[song.alias] = song

    def copy_from(self, other) -> None:
        self.playlists.clear()
        self.song_map.clear()

        self.playlists.update(other.playlists)
        self.song_map.update(other.song_map)

    def get_song(self, song_alias: str) -> Song:
        """Returns a song from the map."""
        if song_alias not in self.song_map:
            raise NotFoundException(
                "Could not find song '%s' in map of songs: '%s'" % (song_alias, self.song_map.keys()))
        song = self.song_map[song_alias]
        return Song(alias=song.alias, uri=song.uri)

    def list_songs(self) -> List[Song]:
        """Lists all songs as Song objects in the current library.

        This will need to be optimized eventually.
        """
        return sorted(list(self.song_map.values()), key=lambda s: s.alias)

    def list_playlists(self) -> List[Tuple[str, List[str]]]:
        """Lists all playlists. Returns a list of tuples containing the Playlist name and a list of Song aliases."""
        return sorted(list((key, self.playlists[key]) for key in self.playlists.keys()), key=lambda t: t[0])

    def get_playlist(self, playlist_name: str) -> List[str]:
        """Returns a list of songs for the corresponding input playlist name."""
        if playlist_name not in self.playlists:
            raise NotFoundException("Playlist '%s' not found in playlist collection '%s'" % (
                playlist_name, self.playlists.keys()))
        return list(self.playlists[playlist_name])

    def get(self, name: str) -> List[str]:
        """Gets whatever the identifier points at - preferring playlists, then falling back to individual songs.

        If the identifier refers to a song, return a list with just that song. Otherwise, return the full playlist.
        """
        if name in self.playlists:
            return [self.get_song(song).uri for song in self.get_playlist(name)]
        if name not in self.playlists.keys() and name not in self.song_map.keys():
            raise NotFoundException("Could not find item in playlists: '%s'\nor songs: '%s'" % (
                                    self.playlists.keys(), self.song_map.keys()))

        return [self.get_song(name).uri]

    def create_playlist(self, playlist_name: str, expect_overwrite: bool = False) -> None:
        """Creates a new playlist with the given playlist_name. Can optionally overwrite an existing playlist."""
        if playlist_name == "":
            raise IllegalArgument("Expected name for playlist, got \"\"")
        existing_playlist = self.playlists.get(playlist_name, None)
        if existing_playlist is not None and not expect_overwrite:
            raise AlreadyExistsException(
                "Playlist '%s' already exists! {%s}" % (playlist_name, existing_playlist))
        self.playlists[playlist_name] = []

    def add_song_to_playlist(self, song_alias: str, playlist_name: str) -> None:
        """Add a song to a playlist based on the input song_alias. The song alias must already exist in the library."""
        if playlist_name not in self.playlists:
            raise NotFoundException("Couldn't find playlist '%s' when adding song '%s'" % (playlist_name, song_alias))
        if not isinstance(song_alias, str):
            raise IllegalArgument(
                "Expected object '%s' of type '%s' to be a string song alias" % (
                    song_alias, type(song_alias).__name__), )
        if song_alias not in self.song_map.keys():
            raise NotFoundException("Couldn't find song '%s'" % (song_alias,))
        self.playlists[playlist_name].append(song_alias)

    def remove_from_playlist(self, song_alias: str, playlist_name: str):
        self.get_playlist(playlist_name).remove(song_alias)


# Used to map schema versions to functions that can understand and handle that schema.
MEDIA_LIBRARY_VERSION_PARSER = {
    1.0: MediaLibrary.parse_v1
}
