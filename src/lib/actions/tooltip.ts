// src/lib/actions/tooltip.ts
export function tooltip(node: HTMLElement, text: string) {
  let tooltipEl: HTMLDivElement | null = null;

  const handleMouseEnter = () => {
    if (!text) return;

    // 1. Create the tooltip bubble element dynamically
    tooltipEl = document.createElement('div');
    tooltipEl.textContent = text;
    
    // 2. Apply styling via modern Tailwind utility classes
    tooltipEl.className = `
      fixed z-50 px-2.5 py-1.5 text-xs text-white 
      bg-zinc-800 border border-white/5 rounded-lg shadow-xl 
      pointer-events-none opacity-0 transition-opacity duration-150 
      whitespace-nowrap tracking-wide
    `;

    document.body.appendChild(tooltipEl);

    // 3. Compute exact pixel coordinates directly above the target node button
    const nodeRect = node.getBoundingClientRect();
    const tooltipRect = tooltipEl.getBoundingClientRect();

    const top = nodeRect.top - tooltipRect.height - 8; // 8px spacer above button
    const left = nodeRect.left + (nodeRect.width / 2) - (tooltipRect.width / 2);

    tooltipEl.style.top = `${top}px`;
    tooltipEl.style.left = `${left}px`;
    
    // Fade in gracefully
    requestAnimationFrame(() => {
      if (tooltipEl) tooltipEl.style.opacity = '1';
    });
  };

  const handleMouseLeave = () => {
    // 4. Safely tear down the element from the DOM when mouse departs
    if (tooltipEl) {
      tooltipEl.remove();
      tooltipEl = null;
    }
  };

  // Attach native window event listeners directly to the host element node
  node.addEventListener('mouseenter', handleMouseEnter);
  node.addEventListener('mouseleave', handleMouseLeave);
  node.addEventListener('click', handleMouseLeave); // Instantly dismiss when button clicked

  return {
    // If the reactive text parameters update dynamically, track changes here
    update(newText: string) {
      text = newText;
    },
    // Lifecycle cleanup: triggered automatically if the button is destroyed from DOM
    destroy() {
      handleMouseLeave();
      node.removeEventListener('mouseenter', handleMouseEnter);
      node.removeEventListener('mouseleave', handleMouseLeave);
      node.removeEventListener('click', handleMouseLeave);
    }
  };
}