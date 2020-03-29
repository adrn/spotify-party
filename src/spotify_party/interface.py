__all__ = ["routes"]

import asyncio
from typing import Any, Mapping, MutableMapping, Optional

from aiohttp import ClientResponseError, web

from . import api, db

routes = web.RouteTableDef()

#
# Actions
#


async def pause_user(
    request: web.Request, user: db.User, device_id: Optional[str] = None
) -> None:
    try:
        await api.call_api(request, user, "/me/player/pause", method="PUT")
    except ClientResponseError as e:
        if e.status != 404:
            raise

        if device_id is None:
            return

        # No active device: transfer first
        await api.call_api(
            request,
            user,
            "/me/player",
            method="PUT",
            json=dict(device_ids=[device_id]),
        )

        await asyncio.sleep(1)
        await pause_user(request, user, device_id)


async def play_user(
    request: web.Request,
    user: db.User,
    data: Mapping[str, Any],
    device_id: Optional[str] = None,
) -> None:
    print("play user")
    try:
        await api.call_api(
            request, user, "/me/player/play", method="PUT", json=data
        )
    except ClientResponseError as e:
        if e.status != 404:
            raise

        if device_id is None:
            return

        # No active device: transfer first
        await api.call_api(
            request,
            user,
            "/me/player",
            method="PUT",
            json=dict(device_ids=[device_id]),
        )

        await asyncio.sleep(1)
        await play_user(request, user, data, device_id)


async def sync_user(
    request: web.Request, user: db.User, device_id: str
) -> None:
    if user.paused:
        return None

    room = await user.listening_to
    if room is None:
        return

    # Get the host's current state
    response = await api.call_api(
        request, room.host, "/me/player/currently-playing"
    )
    if response is None:
        return
    data = response.json()
    print(data)
    uri = data.get("item", {}).get("uri", None)
    position_ms = data.get("progress_ms", None)
    is_playing = data.get("is_playing", False)

    # Update the user's state
    if is_playing and uri is not None and position_ms is not None:
        await play_user(
            request, user, dict(uris=[uri], position_ms=position_ms), device_id
        )
    else:
        await pause_user(request, user, device_id)


async def stop_all(request: web.Request, user: db.User) -> None:
    await stop_streaming(request, user)
    await request.app["db"].stop_listening(user.user_id)


async def stop_streaming(request: web.Request, user: db.User) -> bool:
    room_id = user.playing_to_id
    if room_id is None:
        return False

    # Pause playback for all the listeners
    for listener in await request.app["db"].get_listeners(room_id):
        if listener.paused:
            continue
        await pause_user(request, listener)

    # Update the database
    await request.app["db"].close_room(room_id)

    return True


async def play_stream(
    request: web.Request,
    user: db.User,
    uri: str,
    position_ms: Optional[int] = None,
) -> None:
    room_id = user.playing_to_id
    if room_id is None:
        return

    # Pause playback for all the listeners
    data: MutableMapping[str, Any] = dict(uris=[uri])
    if position_ms is not None:
        data["position_ms"] = position_ms
    for listener in await request.app["db"].get_listeners(room_id):
        if listener.paused:
            continue
        await play_user(request, listener, data)


async def pause_stream(request: web.Request, user: db.User) -> bool:
    room_id = user.playing_to_id
    if room_id is None:
        return False

    # Pause playback for all the listeners
    for listener in await request.app["db"].get_listeners(room_id):
        if listener.paused:
            continue
        await pause_user(request, listener)

    # Update the database
    await request.app["db"].pause_user(user.user_id)

    return True


#
# Endpoints
#


@routes.route("*", "/api/me", name="interface.me")
@api.require_auth(redirect=False)
async def me(request: web.Request, user: db.User) -> web.Response:
    return web.json_response(
        dict(user_id=user.user_id, display_name=user.display_name)
    )


@routes.post("/api/token", name="interface.token")
@api.require_auth(redirect=False)
async def token(request: web.Request, user: db.User) -> web.Response:
    return web.json_response({"token": user.auth.access_token})


#
# Broadcaster endpoints
#


@routes.put("/api/stream", name="interface.stream")
@api.require_auth(redirect=False)
async def stream(request: web.Request, user: db.User) -> web.Response:
    data = await request.json()

    # A device ID is required
    device_id = data.get("device_id", None)
    if device_id is None:
        return web.json_response({"error": "Missing device_id"}, status=400)

    # Stop listening and streaming
    await stop_all(request, user)

    # Transfer playback
    await api.call_api(
        request,
        user,
        "/me/player",
        method="PUT",
        json=dict(device_ids=[device_id]),
    )

    # Create a new room if required
    room = await request.app["db"].get_room(data.get("room_id", None))
    if room is None:
        room_id = await request.app["db"].add_room(user)
    else:
        room_id = room.room_id

    return web.json_response(
        {
            "room_id": room_id,
            "stream_url": str(
                request.url.join(
                    request.app.router["listen"].url_for(room_id=room_id)
                )
            ),
        }
    )


@routes.put("/api/close", name="interface.close")
@api.require_auth(redirect=False)
async def close(request: web.Request, user: db.User) -> web.Response:
    await stop_all(request, user)
    return web.HTTPNoContent()


@routes.put("/api/change", name="interface.change")
@api.require_auth(redirect=False)
async def change(request: web.Request, user: db.User) -> web.Response:
    data = await request.json()
    print(data)

    uri = data.get("uri", None)
    if uri is None:
        return web.json_response({"error": "Missing uri"}, status=400)

    await play_stream(
        request, user, uri, position_ms=data.get("position_ms", None)
    )

    return web.HTTPNoContent()


#
# Listener endpoints
#


@routes.put("/api/play", name="interface.play")
@api.require_auth(redirect=False)
async def play(request: web.Request, user: db.User) -> web.Response:
    data = await request.json()

    # Make sure that the room exists
    room = await request.app["db"].get_room(data.get("room_id", None))
    if room is None:
        return web.json_response({"error": "Invalid room_id"}, status=404)

    # A device ID is required
    device_id = data.get("device_id", None)
    if device_id is None:
        return web.json_response({"error": "Missing device_id"}, status=400)

    # Stop the current user's streams
    await stop_all(request, user)

    # Update the database
    await request.app["db"].listen_to(user.user_id, room.room_id)

    # Synchronize the playback
    user.paused = False
    user.listening_to_id = room.room_id
    await sync_user(request, user, device_id)

    # It worked!
    return web.HTTPNoContent()


@routes.put("/api/pause", name="interface.pause")
@api.require_auth(redirect=False)
async def pause(request: web.Request, user: db.User) -> web.Response:
    if not await pause_stream(request, user):
        await pause_user(request, user)
        await request.app["db"].pause_user(user.user_id)
    return web.HTTPNoContent()


@routes.put("/api/sync", name="interface.sync")
@api.require_auth(redirect=False)
async def sync(request: web.Request, user: db.User) -> web.Response:
    data = await request.json()

    # A device ID is required
    device_id = data.get("device_id", None)
    if device_id is None:
        return web.json_response({"error": "Missing device_id"}, status=400)

    await sync_user(request, user, device_id)

    return web.HTTPNoContent()
