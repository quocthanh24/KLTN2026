// KLTN pinned self-hosted authorization barrier. No absent-list fake ACKs.
package service

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"strconv"
	"sync"
	"time"

	"github.com/livekit/livekit-server/pkg/rtc/types"
	"github.com/livekit/protocol/livekit"
)

var mediaGate sync.RWMutex // join/resume registration vs revoke barrier
var mediaFenced sync.Map   // grant -> expiry, all callbacks still check PostgreSQL
var mediaLive sync.Map     // SID -> entry; removed ONLY after confirmed close
type mediaEntry struct {
	Binding     mediaBinding
	Participant types.LocalParticipant
}

var mediaBoot = func() string {
	b := make([]byte, 16)
	if _, e := rand.Read(b); e != nil {
		panic("boot entropy unavailable")
	}
	return hex.EncodeToString(b)
}()
var mediaNonceMu sync.Mutex
var mediaNonces = map[string]time.Time{}

func mediaIsFenced(b mediaBinding) bool {
	if value, ok := mediaFenced.Load(b.Grant); ok {
		return time.Now().Before(value.(time.Time))
	}
	return false
}
func closeConfirmed(p types.LocalParticipant) bool {
	proof, ok := p.(interface{ MediaCloseConfirmed() bool })
	return ok && proof.MediaCloseConfirmed()
}
func registerMedia(p types.LocalParticipant, b mediaBinding) {
	mediaLive.Store(p.ID(), mediaEntry{b, p})
	go func() {
		<-p.Disconnected()
		for !closeConfirmed(p) {
			time.Sleep(20 * time.Millisecond)
		}
		mediaLive.Delete(p.ID())
	}()
}

func init() {
	if addr := os.Getenv("KLTN_MEDIA_CONTROL_ADDR"); addr != "" {
		mux := http.NewServeMux()
		mux.HandleFunc("/revoke", mediaRevoke)
		go func() {
			server := &http.Server{Addr: addr, Handler: mux, ReadHeaderTimeout: 2 * time.Second, ReadTimeout: 3 * time.Second, WriteTimeout: 8 * time.Second, IdleTimeout: 15 * time.Second, MaxHeaderBytes: 8192}
			if server.ListenAndServe() != nil {
				panic("media control listener failed")
			}
		}()
	}
}

func mediaRevoke(w http.ResponseWriter, r *http.Request) {
	if r.Method != "POST" {
		http.Error(w, "method", 405)
		return
	}
	raw, err := io.ReadAll(io.LimitReader(r.Body, 262145))
	if err != nil || len(raw) > 262144 {
		http.Error(w, "size", 413)
		return
	}
	key, err := os.ReadFile(os.Getenv("KLTN_MEDIA_AUTHORITY_KEY_FILE"))
	if err != nil || len(key) < 32 {
		http.Error(w, "unavailable", 503)
		return
	}
	stamp, nonce := r.Header.Get("X-Media-Time"), r.Header.Get("X-Media-Nonce")
	millis, err := strconv.ParseInt(stamp, 10, 64)
	if err != nil || len(nonce) != 32 || time.Since(time.UnixMilli(millis)) > 5*time.Second || time.Until(time.UnixMilli(millis)) > 5*time.Second {
		http.Error(w, "auth", 403)
		return
	}
	mac := hmac.New(sha256.New, key)
	mac.Write([]byte(stamp + "\n" + nonce + "\n"))
	mac.Write(raw)
	signature, err := hex.DecodeString(r.Header.Get("X-Media-MAC"))
	if err != nil || !hmac.Equal(signature, mac.Sum(nil)) {
		http.Error(w, "auth", 403)
		return
	}
	mediaNonceMu.Lock()
	for n, t := range mediaNonces {
		if time.Since(t) > 10*time.Second {
			delete(mediaNonces, n)
		}
	}
	_, replay := mediaNonces[nonce]
	if !replay {
		mediaNonces[nonce] = time.Now()
	}
	mediaNonceMu.Unlock()
	if replay {
		http.Error(w, "replay", 403)
		return
	}
	var request struct {
		Operation string   `json:"operation"`
		Grants    []string `json:"grants"`
	}
	if json.Unmarshal(raw, &request) != nil || len(request.Operation) != 36 || len(request.Grants) > 4000 {
		http.Error(w, "schema", 422)
		return
	}
	wanted := map[string]bool{}
	for _, g := range request.Grants {
		if len(g) != 36 {
			http.Error(w, "schema", 422)
			return
		}
		wanted[g] = true
	}
	mediaGate.Lock()
	now := time.Now()
	mediaFenced.Range(func(k, v any) bool {
		if now.After(v.(time.Time)) {
			mediaFenced.Delete(k)
		}
		return true
	})
	for g := range wanted {
		mediaFenced.Store(g, now.Add(time.Hour))
	}
	entries := []mediaEntry{}
	mediaLive.Range(func(_, v any) bool {
		e := v.(mediaEntry)
		if wanted[e.Binding.Grant] {
			entries = append(entries, e)
		}
		return true
	})
	// No pre-barrier join can register later; all later joins must pass the fence
	// AND fresh authority. A boot restart always discards prior authorization.
	mediaGate.Unlock()
	for _, e := range entries {
		go e.Participant.Close(true, types.ParticipantCloseReasonServiceRequestRemoveParticipant, false)
	}
	deadline := time.NewTimer(2500 * time.Millisecond)
	defer deadline.Stop()
	ticker := time.NewTicker(10 * time.Millisecond)
	defer ticker.Stop()
	for {
		done := true
		for _, e := range entries {
			if !closeConfirmed(e.Participant) {
				done = false
				break
			}
		}
		if done {
			break
		}
		select {
		case <-r.Context().Done():
			return
		case <-deadline.C:
			http.Error(w, "pending", 503)
			return
		case <-ticker.C:
		}
	}
	closed := []string{}
	for _, e := range entries {
		closed = append(closed, string(e.Participant.ID()))
	}
	response, _ := json.Marshal(map[string]any{"operation": request.Operation, "boot_id": mediaBoot, "pending": false, "barrier": "transport-closed-v1", "fenced_grants": request.Grants, "closed_sids": closed, "closed_at_ms": time.Now().UnixMilli()})
	proof := hmac.New(sha256.New, key)
	proof.Write([]byte(nonce + "\n"))
	proof.Write(response)
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("X-Media-Decision-MAC", hex.EncodeToString(proof.Sum(nil)))
	w.Write(response)
}

var _ livekit.ParticipantID // keep protocol identity type pinned by compiler
