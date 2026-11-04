/**
 * The composer model menu: providers first, then their models.
 *
 * The operator picks models through the provider that serves them, so the
 * menu is grouped by provider and every model carries a checkbox. Only
 * models that have been VERIFIED (measured Runnable) are offered: an
 * unmeasured or broken model is not a choice the operator can make
 * responsibly, and listing it invites a run that cannot work.
 *
 * A model that is configured but not yet verified is counted, so the menu
 * can say how many are waiting to be measured instead of hiding them.
 */

export interface ComposerModelCandidate {
  /** The model reference used by the composer contract ("<provider>:<model>"). */
  ref: string;
  /** The model id as the provider knows it. */
  model: string;
  /** The provider that serves the model. */
  provider: string;
  /** The label shown to the operator. */
  label: string;
  /** True when the model has been measured and is usable. */
  verified: boolean;
  /**
   * Short health note shown next to the model ("Auth required",
   * "Temporarily unavailable"). Empty when the model is healthy.
   */
  healthNote?: string;
  /**
   * True when the last recorded verdict was a transient provider failure
   * (down, rate-limited, timed out) rather than a configuration defect.
   * Such models stay selectable but sort below healthy ones.
   */
  degraded?: boolean;
}

export interface ComposerModelGroup {
  /** The provider name — the group heading. */
  provider: string;
  models: ComposerModelCandidate[];
}

export interface ComposerModelMenu {
  groups: ComposerModelGroup[];
  /** How many configured models are still waiting to be measured. */
  unverifiedCount: number;
}

/**
 * Group the candidates by provider, keeping the provider order the
 * operator configured and dropping everything that is not verified.
 * Within each provider, degraded (transient-failure) models sort below
 * healthy ones so the operator reaches for a working model first.
 */
export function buildComposerModelMenu(
  candidates: ComposerModelCandidate[],
): ComposerModelMenu {
  const groups: ComposerModelGroup[] = [];
  const byProvider = new Map<string, ComposerModelGroup>();
  let unverifiedCount = 0;
  for (const candidate of candidates) {
    if (!candidate.verified) {
      unverifiedCount += 1;
      continue;
    }
    let group = byProvider.get(candidate.provider);
    if (!group) {
      group = { provider: candidate.provider, models: [] };
      byProvider.set(candidate.provider, group);
      groups.push(group);
    }
    group.models.push(candidate);
  }
  for (const group of groups) {
    group.models.sort((a, b) => Number(a.degraded === true) - Number(b.degraded === true));
  }
  return { groups, unverifiedCount };
}
