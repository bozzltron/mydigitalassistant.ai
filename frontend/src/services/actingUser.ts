/**
 * The account every user-scoped request is made as.
 *
 * There is no auth layer: `GET /users` lists the household's accounts and memory is
 * scoped by id, so the id has to travel with each request. It is resolved once —
 * `fetchUser` sets it — and read here, rather than defaulted at every call site.
 *
 * What this replaces: `user_id = 1` as a parameter default, on the client and on the
 * server. That assumed an account with id 1 existed (it need not) and that it was
 * the one acting (it need not be) — so a request could write one household member's
 * data under another's name, or fail a foreign key on an install whose only account
 * happens to be id 3.
 *
 * This module deliberately imports nothing, so `services/api.ts` can read it without
 * the cycle that importing `state/user.ts` would create.
 */

let actingUserId: number | null = null

/** Set the acting account, or `null` when none is loaded. Called by `fetchUser`. */
export function setActingUser(id: number | null): void {
  actingUserId = id
}

/**
 * The acting account's id.
 *
 * Throws rather than substituting a default: a request that cannot say who it is
 * for has no correct answer, and a silent guess is exactly the bug this replaced.
 */
export function actingUser(): number {
  if (actingUserId === null) {
    throw new Error('No account is loaded, so no user-scoped request can be made')
  }
  return actingUserId
}

/** The id if one is loaded, else `null` — for a caller that can degrade gracefully. */
export function actingUserOrNull(): number | null {
  return actingUserId
}
