import { TrackInfo } from "./types";

interface CallOptions {
  data?: any;
  callback?: (data?: any) => void;
  error?: (message?: string) => void;
}

interface APIArgs {
  method: string;
  headers: { "Content-Type": string };
  body?: string;
}

export class API {
  call(endpoint: string, options?: CallOptions) {
    let args: APIArgs = {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      }
    };
    if (options?.data) args.body = JSON.stringify(options.data);
    fetch(endpoint, args)
      .then(response => response.json())
      .then(data => {
        if (data.error && options?.error) options.error(data.error);
        else if (options?.callback) options.callback(data);
      })
      .catch(error =>
        console.error(`call to '${endpoint}' failed with error: ${error})`)
      );
  }

  getToken(callback: (token: string) => void) {
    this.call("/api/token", { callback: data => callback(data.token) });
  }

  startBroadcast(device_id: string, room_id?: string) {}

  stream(
    device_id: string,
    room_id?: string,
    callback?: (
      room_id: string,
      stream_url: string,
      playing?: TrackInfo
    ) => void
  ) {
    const req = fetch("/api/stream", {
      method: "PUT",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ device_id: device_id, room_id: room_id })
    }).then(response => response.json());
    if (callback)
      req.then(data => callback(data.room_id, data.stream_url, data.playing));
    req.catch(error => console.log(`couldn't get stream: ${error}`));
  }

  close(callback?: () => void) {
    const req = fetch("/api/close", { method: "PUT" });
    if (callback) req.then(() => callback());
    req.catch(error => console.log(`couldn't close: ${error}`));
  }

  change(data: TrackInfo, callback?: () => void) {
    const req = fetch("/api/change", {
      method: "PUT",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(data)
    });
    if (callback) req.then(() => callback());
    req.catch(error => console.log(`couldn't change: ${error}`));
  }

  listen(
    deviceId: string,
    roomId: string,
    callback?: (listeners: number, data: TrackInfo) => void
  ) {
    const req = fetch("/api/listen", {
      method: "PUT",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ device_id: deviceId, room_id: roomId })
    }).then(response => response.json());
    if (callback) req.then(data => callback(data.number, data.playing));
    req.catch(error => console.log(`couldn't listen: ${error}`));
  }

  pause(callback?: () => void) {
    const req = fetch("/api/pause", { method: "PUT" });
    if (callback) req.then(() => callback());
    req.catch(error => console.log(`couldn't pause: ${error}`));
  }

  sync(deviceId: string, callback?: (data: TrackInfo) => void) {
    const req = fetch("/api/sync", {
      method: "PUT",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ device_id: deviceId })
    }).then(response => response.json());
    if (callback) req.then(data => callback(data));
    req.catch(error => console.log(`couldn't sync: ${error}`));
  }

  transfer(deviceId: string, callback?: () => void) {
    const req = fetch("/api/transfer", {
      method: "PUT",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ device_id: deviceId })
    });
    if (callback) req.then(() => callback());
    req.catch(error => console.log(`couldn't transfer: ${error}`));
  }
}
