/**
 * Limit automatic onboarding to the app's initial route. Returning home after
 * skipping or saving onboarding must not repeat the same redirect this run.
 */
export function createStartupOnboardingGuard(shouldOpenOnboarding) {
  let startupRouteHandled = false

  return async to => {
    if (startupRouteHandled) return true
    startupRouteHandled = true
    if (to.name !== 'home') return true
    return (await shouldOpenOnboarding()) ? { name: 'onboarding', replace: true } : true
  }
}
