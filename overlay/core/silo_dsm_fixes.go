package web

// Added by Silo DSM (overlay/apply.py): fixes for Silo.1 until upstream has them.

import (
	"context"
	"encoding/hex"
	"net/http"

	"silo/internal/ffmpeg/hls"
	"silo/internal/log"

	"github.com/gin-gonic/gin"
)

// siloDSMValidHash answers 400 for a :hash that is not 40 hex characters;
// a short one made the torrent handlers panic.
func siloDSMValidHash() gin.HandlerFunc {
	return func(c *gin.Context) {
		if h := c.Param("hash"); h != "" {
			if _, err := hex.DecodeString(h); len(h) != 40 || err != nil {
				c.AbortWithStatusJSON(http.StatusBadRequest, gin.H{"error": "invalid torrent hash"})
				return
			}
		}
		c.Next()
	}
}

// siloDSMWait waits for a segment just ahead of the running ffmpeg.
//
// Upstream waits hlsSegmentWait (15 s) once and then restarts ffmpeg at the
// requested segment. A slow transcode (4K HDR to 1080p with tone mapping) or
// a start after a seek can need longer than that for one segment, and the
// restart throws away the work in progress, so the next segment is late
// again: a restart every 15-20 s and stuttering playback.
//
// Here the wait goes on while ffmpeg is alive and either still starting (no
// segment of its own yet) or has made a new segment during the last
// hlsSegmentWait, until the request ends. A job the throttler paused is
// resumed: the player is waiting for it. A stuck ffmpeg (no new segment in
// hlsSegmentWait) still leads to a restart, as upstream.
func (h *hlsSession) siloDSMWait(ctx context.Context, s *Server, dir, name string, index, start int, done <-chan struct{}) bool {
	last := lastSegmentIndex(dir)
	for {
		h.mu.Lock()
		j := h.job
		h.mu.Unlock()
		if j != nil && j.Paused() {
			_ = j.Resume()
		}

		if err := hls.WaitForSegment(h.playlistPath(s), dir, name, hlsSegmentWait, done); err == nil {
			return true
		}

		select {
		case <-done:
			return false
		case <-ctx.Done():
			return false
		default:
		}

		now := lastSegmentIndex(dir)
		starting := now < start
		if index > now+hlsForwardGap || (!starting && now <= last) {
			log.Debugf("[Web] Silo DSM: session %s gives up on segment %d (ffmpeg from %d, ready up to %d, was %d)",
				h.ID, index, start, now, last)
			return false
		}
		log.Debugf("[Web] Silo DSM: session %s still waits for segment %d (ready up to %d)", h.ID, index, now)
		last = now
	}
}

// siloDSMSetCursor remembers the segment the player asked for.
func (h *hlsSession) siloDSMSetCursor(s *Server, index int) {
	h.mu.Lock()
	h.siloDSMCursor = index + 1
	start := h.startIndex
	h.mu.Unlock()
	log.Debugf("[Web] Silo DSM: session %s wants segment %d (ffmpeg from %d, ready up to %d)",
		h.ID, index, start, lastSegmentIndex(h.workDir(s)))
}

// siloDSMCleanupFrom is the segment the cleanup window is counted from;
// h.mu is held by the caller.
//
// Upstream counts it from h.startIndex, the segment ffmpeg was started at.
// That one does not move while the film plays, so the window ends at
// start + hlsForwardGap: every segment after the 12th since the start is
// deleted at the next playlist request, also the ones ffmpeg has just made
// and the player is about to fetch. The player then waits, ffmpeg is
// restarted, and the same happens 50-60 s later. The player's position moves
// with the film.
func (h *hlsSession) siloDSMCleanupFrom(s *Server) int {
	from := h.startIndex
	if h.siloDSMCursor > 0 {
		from = h.siloDSMCursor - 1
	}
	log.Debugf("[Web] Silo DSM: session %s playlist requested, keeping segments %d..%d",
		h.ID, from-s.segmentTail()+1, from+hlsForwardGap)
	return from
}
