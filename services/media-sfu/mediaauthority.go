// Isolated research patch, not a production authorization implementation.
package service

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/livekit/livekit-server/pkg/rtc"
	"github.com/livekit/livekit-server/pkg/rtc/types"
	"github.com/livekit/protocol/auth"
)

const mediaLease = 2 * time.Second
const mediaRenew = 700 * time.Millisecond

var mediaUnavailable = errors.New("media authority unavailable")

type mediaBinding struct {
	Grant    string `json:"grant"`
	Identity string `json:"identity"`
	Room     string `json:"room"`
	Purpose  string `json:"purpose,omitempty"`
}

var mediaBindings sync.Map // participant SID -> immutable signed binding
var mediaHTTP = &http.Client{Timeout: 450 * time.Millisecond, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}

func bindingFor(grants *auth.ClaimGrants) (mediaBinding, error) {
	var b mediaBinding
	if grants == nil || grants.Video == nil || grants.Video.GetCanUpdateOwnMetadata() {
		return b, fmt.Errorf("media authorization binding required")
	}
	var m struct {
		Grant string `json:"kltn_grant"`
	}
	if json.Unmarshal([]byte(grants.Metadata), &m) != nil || len(m.Grant) != 36 {
		return b, fmt.Errorf("media authorization binding required")
	}
	b = mediaBinding{m.Grant, grants.Identity, grants.Video.Room, "join"}
	return b, nil
}

// A response cannot extend a lease: expiry is measured from request START.
// A dead authority never reuses a cached allow decision for new connections.
func checkMedia(ctx context.Context, b mediaBinding) (time.Time, error) {
	start := time.Now()
	endpoint := os.Getenv("KLTN_MEDIA_AUTHORITY")
	key, err := os.ReadFile(os.Getenv("KLTN_MEDIA_AUTHORITY_KEY_FILE"))
	if err != nil || len(key) < 32 || endpoint == "" {
		return start, mediaUnavailable
	}
	payload, _ := json.Marshal(b)
	nonce := make([]byte, 16)
	if _, err = rand.Read(nonce); err != nil {
		return start, err
	}
	stamp, n := fmt.Sprint(time.Now().UnixMilli()), hex.EncodeToString(nonce)
	mac := hmac.New(sha256.New, key)
	mac.Write([]byte(stamp + "\n" + n + "\n"))
	mac.Write(payload)
	ctx, cancel := context.WithTimeout(ctx, 450*time.Millisecond)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, "POST", endpoint, bytes.NewReader(payload))
	if err != nil {
		return start, mediaUnavailable
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Media-Time", stamp)
	req.Header.Set("X-Media-Nonce", n)
	req.Header.Set("X-Media-MAC", hex.EncodeToString(mac.Sum(nil)))
	resp, err := mediaHTTP.Do(req)
	if err != nil {
		return start, mediaUnavailable
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 1025))
	proof := hmac.New(sha256.New, key)
	proof.Write([]byte(n + "\n"))
	proof.Write(raw)
	expectedProof, proofErr := hex.DecodeString(resp.Header.Get("X-Media-Decision-MAC"))
	if proofErr != nil || !hmac.Equal(expectedProof, proof.Sum(nil)) {
		return start, mediaUnavailable
	}
	var decision struct {
		Allow bool `json:"allow"`
	}
	if err != nil || len(raw) > 1024 || resp.StatusCode != 200 || json.Unmarshal(raw, &decision) != nil || !decision.Allow {
		return start, fmt.Errorf("media authority denied")
	}
	expiry := start.Add(mediaLease)
	if time.Now().After(expiry) {
		return start, fmt.Errorf("media lease expired")
	}
	return expiry, nil
}

func authorizeMedia(ctx context.Context, grants *auth.ClaimGrants) (mediaBinding, time.Time, error) {
	b, err := bindingFor(grants)
	if err != nil {
		return b, time.Time{}, err
	}
	deadline, err := checkMedia(ctx, b)
	return b, deadline, err
}

// Independent of rtcSessionWorker: signaling EOF must not stop lease enforcement.
func monitorMedia(room *rtc.Room, p types.LocalParticipant, b mediaBinding, deadline time.Time) {
	defer mediaBindings.Delete(p.ID())
	expired := time.NewTimer(time.Until(deadline))
	defer expired.Stop()
	renew := time.NewTicker(mediaRenew)
	defer renew.Stop()
	type result struct {
		deadline time.Time
		err      error
	}
	decisions := make(chan result, 1)
	pending := false
	closeMedia := func(reason string) {
		// Close revokes publication; room removal uses SID to avoid kicking a newer tab.
		p.GetLogger().Infow("media authority teardown", "policyReason", reason)
		_ = p.Close(true, types.ParticipantCloseReasonServiceRequestRemoveParticipant, false)
		room.RemoveParticipant(p.Identity(), p.ID(), types.ParticipantCloseReasonServiceRequestRemoveParticipant)
	}
	for {
		select {
		case <-p.Disconnected():
			return
		case <-expired.C:
			closeMedia("lease_expired")
			return
		case <-renew.C:
			if !pending {
				pending = true
				go func() {
					leaseBinding := b
					leaseBinding.Purpose = "lease"
					d, e := checkMedia(context.Background(), leaseBinding)
					decisions <- result{d, e}
				}()
			}
		case res := <-decisions:
			pending = false
			// A transient failure may consume the REMAINING, already-issued lease;
			// it never extends it. New joins/refresh still require a fresh allow.
			if errors.Is(res.err, mediaUnavailable) {
				continue
			}
			if res.err != nil || time.Now().After(deadline) {
				closeMedia("denied_or_unavailable")
				return
			}
			deadline = res.deadline
			if !expired.Stop() {
				select {
				case <-expired.C:
				default:
				}
			}
			expired.Reset(time.Until(deadline))
		}
	}
}

func refreshMediaBinding(p types.LocalParticipant) (string, error) {
	value, ok := mediaBindings.Load(p.ID())
	if !ok {
		return "", fmt.Errorf("media binding missing")
	}
	b := value.(mediaBinding)
	b.Purpose = "refresh"
	if _, err := checkMedia(context.Background(), b); err != nil {
		return "", err
	}
	raw, _ := json.Marshal(map[string]string{"kltn_grant": b.Grant})
	return strings.TrimSpace(string(raw)), nil
}
