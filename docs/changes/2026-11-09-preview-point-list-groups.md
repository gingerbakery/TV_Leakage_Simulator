# Leak point List presentation groups

The numeric `경로 N` field is a detected sample count, not a route identity. It
now includes the `회` suffix. The list has two view modes: individual Points and
similar-route groups. The user explicitly chose physical-route similarity;
equal-detection-count grouping is not exposed or used. Individual view remains
the default. Grouping and filtering change presentation only; raw results,
candidate IDs, optical totals and individual Point numbering are retained.

Similar-route groups compare source/visible endpoints and sampled interior path
shape using the existing distance tolerance. The observer endpoint is excluded
so different viewing angles can share a similar route. Missing paths remain
separate. These are geometric similarity groups, not certification of identical
source identities, openings or physical optical paths. Equal counts alone never
establish physical route identity.

Groups start collapsed. Their title selects all member Point IDs for simultaneous
list/viewer emphasis, including exit guides. Plus/minus reveals the children.
The group filter restricts the displayed groups. Children paginate at 100 rows;
arrow navigation and viewer picks reveal the correct group and child page.
Selection membership uses sets to avoid quadratic highlight work.

Group selection intentionally has no implicit single Receiver/analysis target.
Choose an individual Point to use its existing Ray, Receiver, validation or
precision actions. Individual selection clears group selection. Deletion removes
stale selected IDs. New results, case restoration and clearing reset bulk
selection. Group selection, group expansion and filtering are current-view state
and are not added to the saved project schema.

Tests cover equal counts versus different routes, missing-path separation,
non-destructive grouping, bulk selection, filtered groups, 205-member child
pagination, keyboard/viewer reveal, selection clearing and project round trips.
No live CAD/browser visual QA or GPU tracing is claimed.
