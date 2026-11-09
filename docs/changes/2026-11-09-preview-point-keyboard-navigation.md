# Leak point List keyboard navigation

The list container and Point title buttons support ArrowUp/ArrowDown selection
and Home/End jumps. Movement clamps at the first/last point, crosses 100-row page
boundaries, focuses the selected title and scrolls its row into view. Clicking a
row body focuses its title so the next arrow can navigate immediately.

Navigation is scoped to the list, with no global viewer key listener. Inputs,
editable content, details, unrelated buttons and modified shortcuts retain their
own keyboard behavior. Selection uses the existing store action, including
viewer highlight synchronization. Shortcut explanation is behind (?) help.

Panel regression tests cover page crossings, focus, boundaries, Home/End and
preservation of detail/delete controls. The 31 panel tests, typecheck and
production build passed. No live browser visual QA or server restart was done.
