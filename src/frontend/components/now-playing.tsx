import React from "react";
import { TrackInfo } from "../types";

export function NowPlaying(props: {
  listeners: number;
  trackInfo?: TrackInfo;
}) {
  const listeners = props.listeners ? props.listeners : "None (yet!)";
  if (props.trackInfo) {
    const url = `https://open.spotify.com/${props.trackInfo.type}/${props.trackInfo.id}`;
    return (
      <p>
        Now playing:{" "}
        <a target="_blank" rel="noopener noreferrer" href={url}>
          {props.trackInfo.name}
        </a>
        <br />
        Listeners: {listeners}
      </p>
    );
  }
  return (
    <p>
      Now playing: Nothing.
      <br />
      Listeners: {listeners}
    </p>
  );
}
