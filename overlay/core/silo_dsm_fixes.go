package web

// Added by Silo DSM (overlay/apply.py): fixes for Silo.1 until upstream has
// them.

import (
	"encoding/hex"
	"net/http"

	"github.com/gin-gonic/gin"
)

// siloDSMValidHash answers 400 for a :hash that is not a 40-character hex
// info hash. Without it a short hash reaches metainfo.NewHashFromHex, which
// panics (seen as "hash hex string has bad length" in the log).
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
