# Scoped maintenance conclusions and preview evidence

```powershell
aow4mod maintenance-review "<workspace>" --preview "<workspace/previews/completed job>" --note "History comparison and launcher lifecycle verified; remaining native/environment findings unresolved. No gameplay or publication approval."
aow4mod maintenance-status "<workspace>"
aow4mod maintenance-prepare-release "<workspace>"
```

`maintenance-review` creates a new immutable `reviews/<id>/` record. It binds the exact candidate manifest, current source inventory, assessment artifact hashes and selected context. A new build, context or assessment invalidates the earlier association; original records remain on disk. A nonempty note is required. `--preview` is optional; without it lifecycle coverage is explicitly `not_evaluated`.

The conclusion is derived from the existing manifest, not a user-supplied pass flag. History, environment, native and lifecycle dispositions stay separate. Existing review-required findings remain `unresolved`; zero native serialization errors do not erase diagnostic counts. Compatibility resolved, gameplay verified and publication authorized remain false. Notes can explain decisions but cannot clear gates or grant broader scope.

For preview coverage, a private copy of the completed job's evidence is frozen. The verifier reads original SQLite/configuration/install witnesses and both saved actual launcher sessions. It verifies package UUID, one exact enabled-path replacement, original disabled/candidate enabled, healthy renderer controls and Steam log markers, and archived withdrawal rows/member order/configuration bytes. Result booleans alone are insufficient: saved checkpoint hashes/paths and actual database/configuration agreement are checked. Application pairing/signature records must match the reviewed profile. These are archived tool observations, not independent cryptographic attestation of the operating system.
