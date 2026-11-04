"""Apply a small, version-checked patch to a disposable upstream clone only."""
import pathlib, shutil, subprocess, sys
root = pathlib.Path(sys.argv[1]).resolve()
if subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip() != '0508ee9f9ce71b497e65bbe66037cd2a2090ce5e':
    raise SystemExit('Wrong upstream commit')
target=root/'pkg/service/roommanager.go'
s=target.read_text()
if 'authorizeMedia(ctx, pi.Grants)' in s: raise SystemExit('Already patched')
s=s.replace('sessionStartTime := time.Now()', '''sessionStartTime := time.Now()
 // All participant ingress, including signaling resume, funnels through here.
 if pi.Identity != "" {
  mediaGate.RLock()
  defer mediaGate.RUnlock()
  if _, _, err := authorizeMedia(ctx, pi.Grants); err != nil { return err }
  b, err := bindingFor(pi.Grants)
  if err != nil || mediaIsFenced(b) { return fmt.Errorf("media grant fenced") }
 }
''',1)
s=s.replace('// join room\n', '''// Check again immediately before room.Join, then pin the immutable binding.
 binding, leaseDeadline, authErr := authorizeMedia(ctx, pi.Grants)
 if authErr != nil { _ = participant.Close(false, types.ParticipantCloseReasonJoinFailed, false); return authErr }
 mediaBindings.Store(participant.ID(), binding)
 registerMedia(participant,binding)
 go monitorMedia(room, participant, binding, leaseDeadline)
 // join room
''',1)
s=s.replace('grants := participant.ClaimGrants()\n\ttoken :=', '''metadata, authErr := refreshMediaBinding(participant)
 if authErr != nil { return authErr }
 grants := participant.ClaimGrants()
 token :=''')
s=s.replace('SetMetadata(grants.Metadata).','SetMetadata(metadata).',1)
needle='participant.GetLogger().Infow(\n\t\t\t\t"resuming RTC session",'
assert needle in s
s=s.replace(needle,'''originalBinding, pinned := mediaBindings.Load(participant.ID())
   requestedBinding, bindErr := bindingFor(pi.Grants)
   if bindErr != nil || !pinned || originalBinding.(mediaBinding).Grant != requestedBinding.Grant {
    return fmt.Errorf("media resume binding mismatch")
   }
   '''+needle,1)
target.write_text(s)
# Reject at validation too (before WS upgrade), but StartSession is the final guard.
p=root/'pkg/service/utils.go'; s=p.read_text()
needle='res.grants = claims\n\treturn res, http.StatusOK, nil'
assert needle in s
s=s.replace(needle,'''if _, _, err := authorizeMedia(r.Context(), claims); err != nil {
  return res, http.StatusForbidden, err
 }
 res.grants = claims
 return res, http.StatusOK, nil''',1)
p.write_text(s)
shutil.copyfile(pathlib.Path(__file__).with_name('mediaauthority.go'),root/'pkg/service/mediaauthority.go')
shutil.copyfile(pathlib.Path(__file__).with_name('mediaack.go'),root/'pkg/service/mediaack.go')
# Completion flag is set only after the actual Pion PC.Close succeeds.
p=root/'pkg/rtc/transport.go';s=p.read_text()
s=s.replace('isClosed  atomic.Bool','isClosed  atomic.Bool\n mediaClosed atomic.Bool',1)
needle='if err := t.pc.Close(); err != nil {\n\t\tt.params.Logger.Warnw("unclean close of peer connection", err)\n\t}'
assert needle in s
s=s.replace(needle,needle+' else { t.mediaClosed.Store(true) }',1);p.write_text(s)
p=root/'pkg/rtc/transportmanager.go';s=p.read_text()
s+='''
func (t *TransportManager) MediaCloseConfirmed() bool {
 return (t.publisher == nil || t.publisher.mediaClosed.Load()) && (t.subscriber == nil || t.subscriber.mediaClosed.Load())
}
''';p.write_text(s)
p=root/'pkg/rtc/participant.go';s=p.read_text()
needle='disconnected chan struct{}';assert needle in s
s=s.replace(needle,needle+'\n mediaCloseComplete atomic.Bool',1)
needle='p.SubscriptionManager.Close(isExpectedToResume)\n\t\tp.TransportManager.Close()';assert needle in s
s=s.replace(needle,'''tracks := p.SubscriptionManager.GetSubscribedTracks()
  p.SubscriptionManager.Close(isExpectedToResume)
  p.TransportManager.Close()
  for _, track := range tracks { if dt := track.DownTrack(); dt != nil { dt.CloseWithFlush(false, true) } }
  p.mediaCloseComplete.Store(true)''',1)
s+='''
// Successful transport close and completed subscription-manager shutdown are
// required. IsClosed alone is deliberately insufficient for a revocation ACK.
func (p *ParticipantImpl) MediaCloseConfirmed() bool {
 return p.mediaCloseComplete.Load() && p.TransportManager.MediaCloseConfirmed()
}
''';p.write_text(s)
p=root/'pkg/service/auth.go';s=p.read_text()
s=s.replace('errors.New("invalid token: "+authToken+", error: "+err.Error())','errors.New("invalid authorization token")')
p.write_text(s)
print('Patched disposable upstream clone only')
