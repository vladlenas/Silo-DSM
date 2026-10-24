package job

// Added by Silo DSM (overlay/apply.py): fix for Silo.1 until upstream has it.

import (
	"os"
	"path/filepath"
	"strings"
)

// siloDSMProducedFrom tells how many segments ffmpeg has made since the
// segment it was started at: newest segment number - from + 1.
//
// Upstream counts the segment files instead (countSegmentsFrom). Without a
// playlist type ffmpeg runs with -hls_flags delete_segments and keeps only
// the newest 7 files, so that count stops at 7 while the served count keeps
// growing: "ahead" goes below zero after ~10 segments and the throttle never
// pauses ffmpeg again. ffmpeg (1.6x real time on a 4K HDR source) runs away
// from the player and deletes segments the player has not fetched yet; the
// player waits for a file that is gone and playback stops ~60 s after start.
func siloDSMProducedFrom(dir string, from int) int {
	if dir == "" {
		return 0
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		return 0
	}
	newest := from - 1
	for _, e := range entries {
		if e.IsDir() || strings.HasPrefix(e.Name(), ".") {
			continue
		}
		switch strings.ToLower(filepath.Ext(e.Name())) {
		case ".ts", ".m4s":
			if num, ok := trailingSegmentNumber(e.Name()); ok && num > newest {
				newest = num
			}
		}
	}
	return newest - from + 1
}
