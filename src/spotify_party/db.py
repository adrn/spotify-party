__all__ = ["create_tables", "User", "Room", "Database"]

import asyncio
import pathlib
import sqlite3
from typing import (
    Any,
    Dict,
    Iterable,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Union,
)

import aiosqlite
import pkg_resources
from aiohttp import ClientResponseError, web
from aiohttp_spotify import SpotifyAuth

from . import api
from .generate_room_name import generate_room_name

DEFAULT_RETRIES = 3


def create_tables(filename: Union[str, pathlib.Path]) -> None:
    with open(
        pkg_resources.resource_filename(__name__, "schema.sql"), "r"
    ) as f:
        schema = f.read()
    with sqlite3.connect(filename) as connection:
        connection.executescript(schema)


class User:
    def __init__(
        self,
        database: "Database",
        user_id: str,
        display_name: str,
        access_token: str,
        refresh_token: str,
        expires_at: int,
        listening_to: Union[str, None],
        playing_to: Union[str, None],
        paused: int,
        device_id: Union[str, None],
    ):
        self.database = database
        self.user_id = user_id
        self.display_name = display_name
        self.auth = SpotifyAuth(access_token, refresh_token, expires_at)
        self.listening_to_id = listening_to
        self.playing_to_id = playing_to
        self.paused = bool(paused)
        self.device_id = device_id

    @classmethod
    def from_row(
        cls, database: "Database", row: Union[Iterable, None]
    ) -> Union["User", None]:
        if row is None:
            return None
        return cls(database, *row)

    @property
    async def listening_to(self) -> Union["Room", None]:
        return await self.database.get_room(self.listening_to_id)

    @property
    async def playing_to(self) -> Union["Room", None]:
        return await self.database.get_room(self.playing_to_id)

    async def update_auth(self, request: web.Request) -> None:
        changed, auth = await api.update_auth(request, self.auth)
        if changed:
            await self.database.update_auth(self, auth)
            self.auth = auth

    async def set_device_id(self, device_id: str) -> None:
        await self.database.set_device_id(self.user_id, device_id)
        self.device_id = device_id

    async def transfer(
        self, request: web.Request, *, play: bool = False, check: bool = True
    ) -> bool:
        if self.device_id is None:
            return False

        # No active device: transfer first
        try:
            await api.call_api(
                request,
                self,
                "/me/player",
                method="PUT",
                json=dict(device_ids=[self.device_id], play=play),
            )
        except ClientResponseError as e:
            print(f"'/me/player' returned {e.status}")
            return False

        if check:
            await asyncio.sleep(1)
            response = await api.call_api(request, self, "/me/player/devices")
            if response is None:
                return False
            return any(
                device.get("is_active", False)
                and (device.get("id", None) == self.device_id)
                for device in response.json().get("devices", [])
            )

        return True

    async def pause(
        self, request: web.Request, *, retries: int = DEFAULT_RETRIES
    ) -> bool:
        try:
            await api.call_api(request, self, "/me/player/pause", method="PUT")

        except ClientResponseError as e:
            if e.status not in (403, 404):
                raise
            return False

        return True

    async def stop(self, request: web.Request) -> bool:
        """Like pause, but update the database too"""
        room = await self.playing_to
        if room is None:
            flag = await self.pause(request)
        else:
            flag = await room.stop(request)

        await request.app["db"].stop(self.user_id)

        return flag

    async def play(
        self,
        request: web.Request,
        data: Mapping[str, Any],
        *,
        retries: int = DEFAULT_RETRIES,
    ) -> bool:
        try:
            await api.call_api(
                request, self, "/me/player/play", method="PUT", json=data
            )

        except ClientResponseError as e:
            if e.status not in (403, 404):
                raise

            flag = await self.transfer(request, play=True, check=False)
            if flag and retries > 0:
                await asyncio.sleep(1)
                return await self.play(request, data, retries=retries - 1)

            return False

        return True

    async def currently_playing(
        self, request: web.Request
    ) -> Union[Dict[str, Any], None]:
        response = await api.call_api(
            request, self, "/me/player/currently-playing"
        )
        if response is None or response.status == 204:
            return None
        data = response.json()
        item = data.get("item", {})
        return {
            "uri": item.get("uri", None),
            "name": item.get("name", None),
            "type": item.get("type", None),
            "id": item.get("id", None),
            "position_ms": data.get("progress_ms", None),
            "is_playing": data.get("is_playing", False),
        }

    async def sync(
        self, request: web.Request, *, retries: int = DEFAULT_RETRIES
    ) -> Union[Mapping[str, Any], None]:
        room = await self.listening_to
        if room is None:
            return None

        data = await room.host.currently_playing(request)
        if data is None:
            return None

        if (
            data.pop("is_playing")
            and data["uri"] is not None
            and data["position_ms"] is not None
        ):
            await self.play(
                request,
                dict(uris=[data["uri"]], position_ms=data["position_ms"]),
                retries=retries,
            )
        else:
            await self.pause(request, retries=retries)

        return data

    async def listen_to(
        self,
        request: web.Request,
        room: "Room",
        device_id: str,
        *,
        retries: int = DEFAULT_RETRIES,
    ) -> Union[Mapping[str, Any], None]:
        await self.set_device_id(device_id)

        await self.stop(request)

        await self.database.listen_to(self.user_id, room.room_id)
        self.listening_to_id = room.room_id
        self.paused = False

        return await self.sync(request, retries=retries)

    async def play_to(
        self, request: web.Request, device_id: str, *, room_name: str
    ) -> Optional[str]:
        await self.set_device_id(device_id)

        await self.stop(request)

        await self.transfer(request)
        flag = await self.play(request, {})
        if not flag:
            return None

        room_id = f"{self.user_id}/{room_name}"
        await self.database.add_room(self, room_id)

        self.listening_to_id = None
        self.playing_to_id = room_id
        self.paused = False

        return room_id


class Room:
    def __init__(self, host: User):
        self.host = host
        self.room_id = host.playing_to_id
        self.host_id = host.user_id

    @classmethod
    def from_row(
        cls, database: "Database", row: Union[Iterable, None]
    ) -> Union["Room", None]:
        if row is None:
            return None
        return cls(User(database, *row))

    @property
    async def listeners(self) -> List[Union[User, None]]:
        return await self.host.database.get_listeners(self.room_id)

    async def play(
        self, request: web.Request, uri: str, position_ms: Optional[int] = None
    ) -> bool:
        data: MutableMapping[str, Any] = dict(uris=[uri])
        if position_ms is not None:
            data["position_ms"] = position_ms

        success = True
        for user in await self.listeners:
            if user is None or user.paused:
                continue
            flag = await user.play(request, data)
            if not flag:
                success = False
        return success

    async def pause(self, request: web.Request) -> bool:
        success = True
        for user in await self.listeners:
            if user is None or user.paused:
                continue
            flag = await user.pause(request)
            if not flag:
                success = False
        return success

    async def stop(self, request: web.Request) -> bool:
        if self.room_id is None:
            return False
        success = await self.host.pause(request)
        success = success and await self.pause(request)
        await self.host.database.close_room(self.room_id)
        return success


class Database:
    def __init__(self, filename: Union[str, pathlib.Path]):
        self.filename = filename

    async def update_auth(self, user: User, auth: SpotifyAuth) -> None:
        async with aiosqlite.connect(self.filename) as conn:
            await conn.execute(
                """UPDATE users SET
                    access_token=?,
                    refresh_token=?,
                    expires_at=?
                WHERE user_id=?""",
                (
                    auth.access_token,
                    auth.refresh_token,
                    auth.expires_at,
                    user.user_id,
                ),
            )
            await conn.commit()

    async def add_user(
        self, user_id: str, display_name: str, auth: SpotifyAuth
    ) -> Union[User, None]:
        async with aiosqlite.connect(self.filename) as conn:
            await conn.execute(
                """
                INSERT INTO users(
                    user_id,display_name,access_token,refresh_token,expires_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    access_token=excluded.access_token,
                    refresh_token=excluded.refresh_token,
                    expires_at=excluded.expires_at
                """,
                (
                    user_id,
                    display_name,
                    auth.access_token,
                    auth.refresh_token,
                    auth.expires_at,
                ),
            )
            await conn.commit()
        return await self.get_user(user_id)

    async def set_device_id(
        self, user_id: Union[str, None], device_id: str
    ) -> None:
        if user_id is None:
            return
        async with aiosqlite.connect(self.filename) as conn:
            await conn.execute(
                "UPDATE users SET device_id=? WHERE user_id=?",
                (device_id, user_id),
            )
            await conn.commit()

    async def get_user(self, user_id: Union[str, None]) -> Union[User, None]:
        if user_id is None:
            return None
        async with aiosqlite.connect(self.filename) as conn:
            async with conn.execute(
                "SELECT * FROM users WHERE user_id=?", (user_id,)
            ) as cursor:
                return User.from_row(self, await cursor.fetchone())

    async def pause_user(self, user_id: Union[str, None]) -> None:
        if user_id is None:
            return
        async with aiosqlite.connect(self.filename) as conn:
            await conn.execute(
                "UPDATE users SET paused=1 WHERE user_id=?", (user_id,)
            )
            await conn.commit()

    async def unpause_user(self, user_id: Union[str, None]) -> None:
        if user_id is None:
            return
        async with aiosqlite.connect(self.filename) as conn:
            await conn.execute(
                "UPDATE users SET paused=0 WHERE user_id=?", (user_id,)
            )
            await conn.commit()

    async def listen_to(
        self, user_id: Union[str, None], room_id: Union[str, None]
    ) -> None:
        if user_id is None or room_id is None:
            return
        async with aiosqlite.connect(self.filename) as conn:
            await conn.execute(
                "UPDATE users SET listening_to=?, paused=0 WHERE user_id=?",
                (room_id, user_id),
            )
            await conn.commit()

    async def stop(self, user_id: Union[str, None]) -> None:
        if user_id is None:
            return
        async with aiosqlite.connect(self.filename) as conn:
            await conn.execute(
                """UPDATE users SET
                  listening_to=NULL, playing_to=NULL
                WHERE user_id=?""",
                (user_id,),
            )
            await conn.commit()

    async def get_room(self, room_id: Union[str, None]) -> Union[Room, None]:
        if room_id is None:
            return None
        async with aiosqlite.connect(self.filename) as conn:
            async with conn.execute(
                "SELECT * FROM users WHERE playing_to=?", (room_id,)
            ) as cursor:
                return Room.from_row(self, await cursor.fetchone())

    async def add_room(self, host: User, room_id: str) -> str:
        async with aiosqlite.connect(self.filename) as conn:
            await conn.execute(
                "UPDATE users SET playing_to=?, paused=0 WHERE user_id=?",
                (room_id, host.user_id),
            )
            await conn.commit()
        return room_id

    async def pause_room(self, room_id: str) -> None:
        async with aiosqlite.connect(self.filename) as conn:
            await conn.execute(
                "UPDATE users SET paused=1 WHERE playing_to=?", (room_id,)
            )
            await conn.commit()

    async def close_room(self, room_id: str) -> None:
        async with aiosqlite.connect(self.filename) as conn:
            await conn.execute(
                "UPDATE users SET playing_to=NULL WHERE playing_to=?",
                (room_id,),
            )
            await conn.execute(
                "UPDATE users SET listening_to=NULL WHERE listening_to=?",
                (room_id,),
            )
            await conn.commit()

    async def get_listeners(
        self, room_id: Union[str, None]
    ) -> List[Union[User, None]]:
        if room_id is None:
            return []
        async with aiosqlite.connect(self.filename) as conn:
            async with conn.execute(
                "SELECT * FROM users WHERE listening_to=?", (room_id,)
            ) as cursor:
                return [User.from_row(self, row) async for row in cursor]

    async def get_room_stats(self) -> Iterable:
        async with aiosqlite.connect(self.filename) as conn:
            async with conn.execute(
                """
                SELECT
                    main.user_id,
                    main.display_name,
                    main.playing_to,
                    count(other.user_id)
                FROM users AS main
                LEFT JOIN users AS other ON
                    main.playing_to = other.listening_to
                WHERE main.playing_to IS NOT NULL
                """
            ) as cursor:
                return await cursor.fetchall()
