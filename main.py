import uuid
from utils.download import DL_STATUS
import aiohttp
from config import *
from utils.remote_upload import start_remote_upload
from utils.tgstreamer import work_loads, multi_clients
import asyncio
from pyrogram import Client, idle
from werkzeug.utils import secure_filename
import os
from utils.db import is_hash_in_db, save_file_in_db
from utils.file import allowed_file, delete_cache, get_file_hash
from utils.tgstreamer import media_streamer
from utils.upload import upload_file_to_channel
from utils.upload import PROGRESS
import aiofiles
import re
import string
import random

from aiohttp import web

app = web.Application()


def generate_random_string(length=8):
    """Generate a random string of lowercase letters and digits."""
    letters_and_digits = string.ascii_lowercase + string.digits
    return ''.join(random.choice(letters_and_digits) for _ in range(length))


def generate_unique_filename(filename):
    """Generate a unique filename by appending a UUID."""
    unique_id = uuid.uuid4().hex[:6]  # Get the first 6 characters of a randomly generated UUID
    name, ext = os.path.splitext(filename)
    return f"{name}_{unique_id}{ext}"


def render_template(name):
    with open(f"templates/{name}") as f:
        return f.read()


async def upload_file(request):
    global UPLOAD_TASK

    data = await request.post()

    filename = data.get("filename")
    if filename is None:
        return web.Response(
            text="No filename provided in the request body.",
            status=400,
            content_type="text/plain"
        )

    reader = await request.multipart()
    field = await reader.next()

    content_disposition = field.headers.get('Content-Disposition')
    if content_disposition:
        original_filename = re.findall('filename="(.+)"', content_disposition)[0]
    else:
        return web.Response(
            text="Content-Disposition header not found.",
            status=400,
            content_type="text/plain"
        )

    if allowed_file(original_filename):
        if original_filename == "":
            return web.Response(
                text="No file selected.", content_type="text/plain", status=400
            )

        original_filename = secure_filename(original_filename)
        extension = original_filename.rsplit(".", 1)[1]

        new_filename = generate_unique_filename(filename)

        try:
            async with aiofiles.open(os.path.join("static/uploads", new_filename), "wb") as f:
                while True:
                    chunk = await field.read_chunk()
                    if not chunk:
                        break
                    await f.write(chunk)
        except Exception as e:
            return web.Response(
                text=f"Error saving file: {str(e)}",
                status=500,
                content_type="text/plain",
            )

        # Save original filename and hash in database
        hash = get_file_hash(new_filename)  # Assuming you have a function to generate a hash for the file
        save_file_in_db(new_filename, hash)
        UPLOAD_TASK.append((hash, new_filename, extension))
        return web.Response(text=new_filename, content_type="text/plain", status=200)
    else:
        return web.Response(
            text="File type not allowed", status=400, content_type="text/plain"
        )


async def home(_):
    return web.Response(text=render_template("minindex.html"), content_type="text/html")


async def bot_status(_):
    json = work_loads
    return web.json_response(json)


async def remote_upload(request):
    global aiosession
    hash = generate_random_string()
    print(request.headers)
    link = request.headers.get("url")

    reader = await request.multipart()
    field = await reader.next()

    if field is None:
        return web.Response(text="No file uploaded.", content_type="text/plain")

    content_disposition = field.headers.get('Content-Disposition')
    if content_disposition:
        filename = re.findall('filename="(.+)"', content_disposition)[0]
    else:
        return web.Response(
            text="Content-Disposition header not found.",
            status=400,
            content_type="text/plain"
        )

    if allowed_file(filename):
        if filename == "":
            return web.Response(
                text="No file selected.", content_type="text/plain", status=400
            )

        filename = secure_filename(filename)
        extension = filename.rsplit(".", 1)[1]

        new_filename = generate_unique_filename(filename)

        try:
            async with aiofiles.open(os.path.join("static/uploads", new_filename), "wb") as f:
                while True:
                    chunk = await field.read_chunk()
                    if not chunk:
                        break
                    await f.write(chunk)
        except Exception as e:
            return web.Response(
                text=f"Error saving file: {str(e)}",
                status=500,
                content_type="text/plain",
            )

        # Save original filename and hash in database
        hash = get_file_hash(new_filename)  # Assuming you have a function to generate a hash for the file
        save_file_in_db(new_filename, hash)
        UPLOAD_TASK.append((hash, new_filename, extension))
        return web.Response(text=new_filename, content_type="text/plain", status=200)
    else:
        return web.Response(
            text="File type not allowed", status=400, content_type="text/plain"
        )


async def file_html(request):
    filename = request.match_info["filename"]
    hash = request.match_info["hash"]
    download_link = f"http://cloud.techzbots.live/dl/{filename}{hash}"
    filename = is_hash_in_db(filename)["filename"]

    return web.Response(
        text=render_template("minfile.html")
        .replace("FILE_NAME", filename)
        .replace("DOWNLOAD_LINK", download_link),
        content_type="text/html",
    )


async def static_files(request):
    return web.FileResponse(f"static/{request.match_info['file']}")


async def process(request):
    global PROGRESS
    filename = request.match_info["filename"]
    hash = request.match_info["hash"]

    data = PROGRESS.get(filename)
    if data:
        if data.get("message"):
            data = {"message": data["message"]}
            return web.json_response(data)
        else:
            data = {"current": data["done"], "total": data["total"]}
            return web.json_response(data)

    else:
        return web.Response(text="Not Found", status=404, content_type="text/plain")


async def remote_status(request):
    global DL_STATUS
    print(DL_STATUS)
    hash = request.match_info["hash"]

    data = DL_STATUS.get(hash)
    if data:
        if data.get("message"):
            data = {"message": data["message"]}
            return web.json_response(data)
        else:
            data = {"current": data["done"], "total": data["total"]}
            return web.json_response(data)

    else:
        return web.Response(text="Not Found", status=404, content_type="text/plain")


async def download(request: web.Request):
    filename = request.match_info["filename"]
    hash = request.match_info["hash"]
    id = is_hash_in_db(filename, hash)
    if id:
        id = id["msg_id"]
        return await media_streamer(request, id)


UPLOAD_TASK = []


async def upload_task_spawner():
    print("Task Spawner Started")
    global UPLOAD_TASK
    while True:
        if len(UPLOAD_TASK) > 0:
            task = UPLOAD_TASK.pop(0)
            loop.create_task(upload_file_to_channel(*task))
            print("Task created", task)
        await asyncio.sleep(1)


async def generate_clients():
    global multi_clients, work_loads

    print("Generating Clients")

    for i in range(len(BOT_TOKENS)):
        bot = Client(
            f"bot{i}",
            api_id=API_KEY,
            api_hash=API_HASH,
            bot_token=BOT_TOKENS[i],
        )
        await bot.start()
        multi_clients[i] = bot
        work_loads[i] = 0
        print(f"Client {i} generated")


async def start_server():
    global aiosession
    print("Starting Server")
    delete_cache()

    app.router.add_get("/", home)
    app.router.add_get("/static/{file}", static_files)
    app.router.add_get("/dl/{filename}{hash}", download)
    app.router.add_get("/file/{filename}{hash}", file_html)
    app.router.add_post("/upload", upload_file)
    app.router.add_get("/process/{filename}{hash}", process)
    app.router.add_post("/remote_upload", remote_upload)
    app.router.add_get("/remote_status/{filename}{hash}", remote_status)
    app.router.add_get("/bot_status", bot_status)

    aiosession = aiohttp.ClientSession()
    server = web.AppRunner(app)

    print("Starting Upload Task Spawner")
    loop.create_task(upload_task_spawner())
    print("Starting Client Generator")
    loop.create_task(generate_clients())

    await server.setup()
    print("Server Started")
    await web.TCPSite(server).start()
    await idle()


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(start_server())
