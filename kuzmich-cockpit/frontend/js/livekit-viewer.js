/* LiveKit viewer — subscribes to camera video via LiveKit SDK.

Works with both index.html (feedVideo) and viewer.html (video).
Token and URL are fetched from backend /api/camera/livekit-token endpoint.
*/
'use strict';

let lkRoom = null;

async function connectLiveKit() {
  // Fetch JWT token + URL from backend
  const identity = 'viewer-' + Date.now();
  const resp = await fetch('/api/camera/livekit-token?identity=' + encodeURIComponent(identity));
  if (!resp.ok) throw new Error('Failed to get LiveKit token: HTTP ' + resp.status);
  const { token, url } = await resp.json();

  console.log('[LK] Connecting to', url, 'as', identity);

  lkRoom = new LivekitClient.Room({
    adaptiveStream: true,
    dynacast: true,
  });

  lkRoom.on(LivekitClient.RoomEvent.TrackSubscribed, (track, pub, participant) => {
    console.log('[LK] Track subscribed:', track.kind, track.name, 'from', participant.identity);
    if (track.kind === 'video') {
      // Support both index.html (feedVideo) and viewer.html (video)
      const v = document.getElementById('feedVideo') || document.getElementById('video');
      if (v) {
        v.srcObject = new MediaStream([track.mediaStreamTrack]);
        v.style.display = '';
        const ph = document.getElementById('feedph') || document.getElementById('placeholder');
        if (ph) ph.style.display = 'none';
        v.play().then(() => {
          if (typeof updateCamStatus === 'function') updateCamStatus('live');
        }).catch(() => {});
      }
    }
  });

  lkRoom.on(LivekitClient.RoomEvent.TrackUnsubscribed, (track) => {
    console.log('[LK] Track unsubscribed:', track.kind);
    if (track.kind === 'video') {
      const v = document.getElementById('feedVideo') || document.getElementById('video');
      if (v) {
        v.srcObject = null;
        v.style.display = 'none';
        const ph = document.getElementById('feedph') || document.getElementById('placeholder');
        if (ph) ph.style.display = '';
      }
    }
  });

  lkRoom.on(LivekitClient.RoomEvent.ParticipantConnected, (participant) => {
    console.log('[LK] Publisher joined:', participant.identity);
    if (typeof updateCamStatus === 'function') updateCamStatus('connecting');
  });

  lkRoom.on(LivekitClient.RoomEvent.ParticipantDisconnected, (participant) => {
    console.log('[LK] Publisher left:', participant.identity);
    if (typeof updateCamStatus === 'function') updateCamStatus('connecting');
  });

  lkRoom.on(LivekitClient.RoomEvent.Disconnected, () => {
    console.log('[LK] Disconnected from room');
    if (typeof updateCamStatus === 'function') updateCamStatus('error');
  });

  lkRoom.on(LivekitClient.RoomEvent.Connected, () => {
    console.log('[LK] Connected to room');
    if (typeof updateCamStatus === 'function') updateCamStatus('connecting');
  });

  await lkRoom.connect(url, token);
  console.log('[LK] Room joined:', lkRoom.name);
}

function disconnectLiveKit() {
  if (lkRoom) {
    console.log('[LK] Disconnecting');
    lkRoom.disconnect();
    lkRoom = null;
  }
}
