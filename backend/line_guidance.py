"""Pure, calibrated line guidance and debounced ordered crossing detection.

No GPIO/UART. A crossing number is NOT an independently identified landmark.
Commissioned route ordering and physical calibration are external prerequisites.
"""
from dataclasses import dataclass
import math
from line_calibration import black_in_physical_order


@dataclass(frozen=True)
class Guidance:
    kind: str
    direction: str = None
    offset: float = None
    black: tuple = ()


class LineGuidance:
    def __init__(self, calibration, *, positions, deadband):
        if (len(positions)!=4 or any(type(x) not in (int,float) or not math.isfinite(x) for x in positions)
                or any(a>=b for a,b in zip(positions,positions[1:])) or not positions[0]<0<positions[-1]):
            raise ValueError('explicit_ordered_probe_positions_required')
        if type(deadband) not in (int,float) or not math.isfinite(deadband) or deadband<0:
            raise ValueError('invalid_deadband')
        black_in_physical_order(calibration['white_levels'],calibration)
        self.calibration,self.positions,self.deadband=calibration,tuple(positions),deadband

    def classify(self, values):
        black=tuple(black_in_physical_order(values,self.calibration))
        indices=[i for i,b in enumerate(black) if b]
        if not indices:return Guidance('lost',black=black)
        if len(indices)==4:return Guidance('crossing',black=black)
        if indices[-1]-indices[0]+1!=len(indices):return Guidance('ambiguous',black=black)
        offset=sum(self.positions[i] for i in indices)/len(indices)
        direction='forward' if abs(offset)<=self.deadband else 'forward_left' if offset<0 else 'forward_right'
        return Guidance('line',direction,offset,black)


class OrderedCrossings:
    """One event per stable all-black crossing, rearmed only after stable clear.

    Once confirmed, retains its identity until clear. It cannot detect a missed
    physical marker or distinguish identical tape marks. Never called localization.
    """
    def __init__(self, marker_order, *, confirm_seconds, confirm_frames, clear_frames, max_gap):
        if not marker_order or len(set(marker_order))!=len(marker_order) or any(not isinstance(x,str) or not x for x in marker_order):
            raise ValueError('explicit_unique_marker_order_required')
        if any(type(x) is not int or x<1 for x in (confirm_frames,clear_frames)):
            raise ValueError('invalid_frame_count')
        if any(type(x) not in (int,float) or not math.isfinite(x) or x<=0 for x in (confirm_seconds,max_gap)):
            raise ValueError('invalid_marker_timing')
        self.order=tuple(marker_order);self.seconds=confirm_seconds;self.frames=confirm_frames
        self.clear_frames=clear_frames;self.max_gap=max_gap
        self.last=None;self.start=None;self.count=0;self.clear=0;self.index=0;self.active=None
        self.fault=None

    def update(self, all_black, sampled_at):
        if self.fault:raise ValueError(self.fault)
        if type(all_black) is not bool or type(sampled_at) not in (int,float) or not math.isfinite(sampled_at):
            self.fault='invalid_crossing_observation';raise ValueError(self.fault)
        if self.last is not None and (sampled_at<=self.last or sampled_at-self.last>=self.max_gap):
            self.fault='crossing_observation_gap_or_replay';raise ValueError(self.fault)
        self.last=sampled_at
        if all_black:
            self.clear=0
            if self.active:return self.active
            if self.start is None:self.start=sampled_at
            self.count+=1
            if self.count>=self.frames and sampled_at-self.start>=self.seconds:
                if self.index>=len(self.order):
                    self.fault='extra_crossing';raise ValueError(self.fault)
                self.active=self.order[self.index];self.index+=1
            return self.active
        self.start=None;self.count=0;self.clear+=1
        if self.clear>=self.clear_frames:self.active=None
        return self.active
