from peewee import (
    BooleanField,
    CharField,
    DateTimeField,
    FloatField,
    ForeignKeyField,
    IntegerField,
    Model,
    SqliteDatabase,
)
import datetime
from config import get_database_path

# check_same_thread=False is required because the backend serves concurrent requests.
db = SqliteDatabase(str(get_database_path()), pragmas={'foreign_keys': 1}, check_same_thread=False)

class BaseModel(Model):
    class Meta:
        database = db

class Artist(BaseModel):
    id = CharField(primary_key=True)
    name = CharField()
    bio = CharField(default="")
    secondary_id = CharField(null=True)

class Album(BaseModel):
    id = CharField(primary_key=True)
    title = CharField()
    artist = ForeignKeyField(Artist, backref='albums')
    release_year = IntegerField(default=0)
    genre = CharField(default="Unknown")
    rating = IntegerField(default=0)
    description = CharField(default="")

class Track(BaseModel):
    id = CharField(primary_key=True)
    title = CharField()
    artist = ForeignKeyField(Artist, backref='tracks')
    album = ForeignKeyField(Album, backref='tracks')
    track_number = IntegerField(default=0)
    disc_number = IntegerField(default=1)
    duration_ms = IntegerField(default=0)
    rating = IntegerField(default=0)
    has_artwork = BooleanField(default=False)
    
class Playlist(BaseModel):
    id = CharField(primary_key=True)
    name = CharField()
    description = CharField(default="")
    created_at = DateTimeField(default=datetime.datetime.now)

class PlaylistTrack(BaseModel):
    playlist = ForeignKeyField(Playlist, backref='items', on_delete='CASCADE')
    track = ForeignKeyField(Track, backref='playlists', on_delete='CASCADE')
    position = IntegerField()

    class Meta:
        indexes = (
            (('playlist', 'position'), True),
        )

class SearchHistory(BaseModel):
    query = CharField()
    timestamp = DateTimeField(default=datetime.datetime.now)

class PlayHistory(BaseModel):
    # Records every time a song is played
    track = ForeignKeyField(Track, backref='history')
    played_at = DateTimeField(default=datetime.datetime.now)
    completion_pct = FloatField(default=0.0)
    
class QueueItem(BaseModel):
    # Tracks the current queue of songs
    track = ForeignKeyField(Track, backref='queue')
    position = IntegerField(default=0)
    queue_type = IntegerField(default=1) # 0: priority, 1: standard, 2: mix (auto-generated)
    added_at = DateTimeField(default=datetime.datetime.now)
    is_current = BooleanField(default=False)
    
    class Meta:
        database = db
        indexes = (
            (('queue_type', 'position'), False),
        )
    

class DatabaseManager:
    def __init__(self):
        # Only connect if the connection is currently closed
        if db.is_closed():
            db.connect()
        db.create_tables([Artist, Album, Track, PlayHistory, QueueItem, SearchHistory, Playlist, PlaylistTrack])

    def upsert_artist(self, **data):
        Artist.insert(**data).on_conflict(
            conflict_target=[Artist.id],
            preserve=[Artist.name, Artist.secondary_id]
        ).execute()

    def upsert_album(self, **data):
        Album.insert(**data).on_conflict_replace().execute()

    def upsert_track(self, **data):
        Track.insert(**data).on_conflict_replace().execute()
        
    def search(self, query: str, limit: int = 5, unified: bool = False):
        """Returns a dictionary of matches across all categories."""
        if not query or len(query) < 2:
            if unified:
                return []
            else:
                return {"artists": [], "albums": [], "tracks": []}
        results = {
            "artists": list(Artist.select().where(Artist.name.contains(query)).limit(limit)),
            "albums": list(Album.select().join(Artist).where(Album.title.contains(query) | Artist.name.contains(query)).limit(limit)),
            "tracks": list(Track.select().join(Artist).switch(Track).join(Album).where(Track.title.contains(query) | Artist.name.contains(query) | Album.title.contains(query)).limit(limit))
        }
        if not unified:
            return results
        
        # Put results in a dictionary tracked with relevance scores
        unified_results = {}
        for category, items in results.items():
            unified_results[category] = {}
            for item in items:
                unified_results[category][item] = 0
                item_name = item.name if hasattr(item, 'name') else item.title
                rating = item.rating if hasattr(item, 'rating') else 0
                if item_name.lower().startswith(query.lower()):
                    unified_results[category][item] += 10
                    if type(item) == Album: unified_results[category][item] += 5 # Push album above track with identical name
                unified_results[category][item] += rating * 2
                
        # Flatten and sort by relevance
        flat_results = []
        for category, items in unified_results.items():
            flat_results.extend(items.items())
        flat_results.sort(key=lambda x: x[1], reverse=True)
        return [item for item, score in flat_results[:limit]]