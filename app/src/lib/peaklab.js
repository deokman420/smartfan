/* Where the peak-value label sits on a chart.

   The label is anchored to the peak itself, but a peak near the left edge
   of a week-long humidity chart lands on top of the y-axis ticks (88.0
   over the 90), and a peak at the right edge can run off the plot. This
   is the general placement: prefer the side with room, and if a tick is
   still close in Y, drop the label below the dot.
*/

/**
 * @param {number} left  peak x, as a percentage of the plot
 * @param {number} top   peak y, as a percentage of the plot (0 is the top)
 * @param {number[]} tickTops  y-percentage of each axis tick
 * @returns {{ xAlign: 'start' | 'center' | 'end', below: boolean }}
 */
export function peakLabLayout(left, top, tickTops = []) {
  let xAlign = 'center';
  if (left < 14) xAlign = 'end';
  else if (left > 86) xAlign = 'start';

  // Near the top of the plot there is no room above the dot.
  let below = top < 16;
  if (xAlign !== 'center' && !below) {
    const close = tickTops.some((t) => Math.abs(t - top) < 8);
    if (close && top < 84) below = true;
  }
  return { xAlign, below };
}
