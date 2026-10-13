"""GreenThread: carbon- and battery-aware multicore job scheduler for Windows 11.

Core idea (R-BACI): pick a (start time, execution mode) for each job so that the
predicted *upper-quantile* finish time meets the deadline, while minimising a
normalised cost of attributable carbon, battery depletion and latency. Carbon
signals are discounted by their freshness, and energy drawn from the battery is
charged at the carbon intensity of the energy that was stored in it.
"""

__version__ = "1.0.0"
