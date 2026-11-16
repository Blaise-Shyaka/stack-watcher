# Stack Watch: design

Mockups: [Stack Watch screens](https://claude.ai/artifact/Hhge5N1RNn7eReE7omSE3A)
(a design canvas; private to the owner until shared).

## Approach

- Controls come from the CloudCrane UI kit (`ccUI`): navigation bars, the tab
  bar, lists, search, sheets, toasts. It draws iPhone or Android controls from the
  same markup and handles dark mode, safe areas, tap sizes and text that follows
  the phone's size.
- A small layer of our own adds the feed's pieces: the item row, badges, stack
  monograms, section headings and the briefing's states.

## Presentation

A finite morning briefing you can scan in two minutes.

- **Today**: date and a one-line count, a row of stack filters, then sections in
  a fixed order:
  1. Act on it: security advisories, breaking changes, removals
  2. Notable releases: major, first stable, new projects
  3. Worth reading: official posts
  4. Routine updates, folded into one row
  5. "You're caught up"
- **Item detail**: the summary ("In short"), highlights from the release notes,
  "Why you're seeing this", and one button to the source.
- **Stacks**: search (names and aliases), the stacks you follow, browse by
  category, and "Request it" when a search finds nothing.
- **States**: first open (pick stacks, with popular one-tap picks), loading
  (last values shown, never a blank screen), offline (last briefing with its
  time), quiet day, error with what to do.

## Item anatomy

1. Monogram: one or two letters and a colour from the catalog. No brand logos.
2. Meta: stack · source · age.
3. Title: what changed, as the source puts it.
4. Summary: the TLDR, two lines at most.
5. Badges: why it matters, always a word, never colour alone.

## Tokens

| Role | Light | Dark | Use |
| --- | --- | --- | --- |
| accent | `#0F766E` | `#2DD4BF` | actions, current tab, notable releases |
| accent-bg / border | `#E6F4F2` / `#B7E0DA` | `#10302C` / `#1F5C55` | notable badges |
| danger | `#B42318` | `#FF8A80` | security, and nothing else |
| danger-bg / border | `#FDECEA` / `#F5C2BD` | `#3B1C1A` / `#6B2B26` | security badges |
| warning | `#9A5B00` | `#F5B65A` | breaking changes, removals |
| warning-bg / border | `#FFF4E0` / `#F3D19C` | `#3A2A10` / `#6B4E1E` | breaking badges |
| text | `#111316` | `#F2F2F7` | titles |
| muted | `#5B606A` | `#A1A1AA` | meta lines, summaries |
| background | `#F2F2F7` | `#0B0B0D` | page |
| surface | `#FFFFFF` | `#1C1C1E` | grouped lists |
| separator | `#E3E3E8` | `#38383A` | hairlines |

Every text pair reaches 4.5:1 in both modes.

Type: the phone's own font (`system-ui`), sizes in rem.
Large title 34/41 bold, section 20/25 bold, item title 17/22 semibold,
summary 15/20, meta 13/18, badge 12/16 semibold.

Space in steps of 4, 8, 16, 24, 32. Radius 12 for groups, 10 for monograms,
pill for badges and chips. Touch targets at least 44 px.

## Rules

- Red means security, nothing else.
- The briefing ends: "You're caught up" is the last thing on the page.
- Every item says where it came from; the detail says why you're seeing it.
- No emoji, no gradients, no shadows on rows.
