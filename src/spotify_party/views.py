__all__ = ["routes"]

from typing import Awaitable, Callable

import aiohttp_jinja2
import aiohttp_session
from aiohttp import web

from . import api, db, interface
from .generate_room_name import generate_room_name

routes = web.RouteTableDef()


#
# Splash and auth flow
#


@routes.get("/", name="index")
async def index(request: web.Request) -> web.Response:
    return aiohttp_jinja2.render_template("splash.html", request, {})


@routes.get("/about", name="about")
async def about(request: web.Request) -> web.Response:
    return aiohttp_jinja2.render_template(
        "about.html", request, {"current_page": "about"}
    )


@routes.get("/premium", name="premium")
async def premium(request: web.Request) -> web.Response:
    return aiohttp_jinja2.render_template("premium.html", request, {})


@routes.get("/login", name="login")
async def login(request: web.Request) -> web.Response:
    return web.HTTPTemporaryRedirect(
        location=request.app.router["play"].url_for()
    )


@routes.get("/logout", name="logout")
async def logout(request: web.Request) -> web.Response:
    session = await aiohttp_session.get_session(request)
    if "sp_user_id" in session:
        del session["sp_user_id"]
    return web.HTTPTemporaryRedirect(
        location=request.app.router["index"].url_for()
    )


#
# Main app
#


@routes.get("/play", name="play")
@api.require_auth
async def play(request: web.Request, user: db.User) -> web.Response:
    # We'll reuse the same room id if the user is already playing
    room_id = user.playing_to_id

    # Stop the user's current playback
    await user.stop(request)

    # Generate a new room name or close the old one
    if room_id is None:
        room_name = generate_room_name()
    else:
        await interface.sio.emit("close", room=room_id)
        room_name = room_id.split("/")[1]

    return aiohttp_jinja2.render_template(
        "play.html",
        request,
        {
            "is_logged_in": True,
            "current_page": "play",
            "user_id": user.user_id,
            "room_name": room_name,
        },
    )


@routes.get("/listen/{user_id}/{room_name}", name="listen")
@api.require_auth
async def listen(request: web.Request, user: db.User) -> web.Response:
    room_id = (
        f"{request.match_info['user_id']}/{request.match_info['room_name']}"
    )

    room = await request.app["db"].get_room(room_id)
    if room is None:
        return aiohttp_jinja2.render_template("notfound.html", request, {})

    # Is the current user the host?
    if room.host_id == user.user_id:
        return web.HTTPTemporaryRedirect(
            location=request.app.router["play"].url_for()
        )

    user_id, room_name = room_id.split("/")
    return aiohttp_jinja2.render_template(
        "listen.html",
        request,
        {"is_logged_in": True, "user_id": user_id, "room_name": room_name},
    )


#
# Stats pages
#


@routes.get("/admin", name="admin")
@api.require_auth(admin=True)
async def admin(request: web.Request, user: db.User) -> web.Response:
    stats = await request.app["db"].get_room_stats()
    return aiohttp_jinja2.render_template(
        "admin.html", request, {"stats": stats}
    )


@routes.get("/admin/{user_id}/{room_name}", name="admin.room")
@api.require_auth(admin=True)
async def admin_room(request: web.Request, user: db.User) -> web.Response:
    room_id = (
        f"{request.match_info['user_id']}/{request.match_info['room_name']}"
    )
    room = await request.app["db"].get_room(room_id)
    if room is None:
        return web.HTTPNotFound()
    return aiohttp_jinja2.render_template(
        "admin.room.html",
        request,
        {"room": room, "listeners": await room.listeners},
    )


#
# Error handler
#


@web.middleware
async def error_middleware(
    request: web.Request, handler: Callable[[web.Request], Awaitable]
) -> web.Response:
    try:
        response = await handler(request)
        if response.status < 400:
            return response
        error_code = response.status
    except web.HTTPException as ex:
        error_code = ex.status
        if error_code < 400:
            raise
    return aiohttp_jinja2.render_template(
        "error.html", request, {"error_code": error_code}
    )
