import { cubicOut } from 'svelte/easing';
import type { EasingFunction, TransitionConfig } from 'svelte/transition';

type SlideXParams = {
    duration?: number;
    easing?: EasingFunction;
    side?: 'left' | 'right';
};

// A real horizontal slide: the node translates fully off its own edge instead of
// animating its width (which Svelte's built-in `slide` does and looks like a
// "grow"). Pair with a coordinated padding transition on the content so the page
// reflows as the panel travels in/out.
export function slideX(
    node: Element,
    { duration = 200, easing = cubicOut, side = 'right' }: SlideXParams = {},
): TransitionConfig {
    const sign = side === 'right' ? 1 : -1;
    return {
        duration,
        easing,
        css: (t) => `transform: translateX(${(1 - t) * 100 * sign}%);`,
    };
}
