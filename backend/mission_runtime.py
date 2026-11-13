"""Single-owner mission execution and durable capture adapter.

Not imported by the production HTTP server. The shipped CLI only uses a simulated
motion port. Actual physical commissioning and an independent stationary input
are still prerequisites for wiring this engine into an on-robot control loop.
"""
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile

from experiment_log import Station, _health, number
from mission_plan import MissionPlanner, TERMINAL


class SimulatedMotion:
    hardware_enabled=False
    def __init__(self):self.commands=[];self.stopped=True
    def drive(self,direction,speed):
        self.commands.append(dict(action='drive',direction=direction,speed=speed));self.stopped=False
    def hold(self):
        self.commands.append(dict(action='hold'));self.stopped=True
    def close(self):self.hold()


class LeasedMotorPort:
    """Adapter for the EXISTING sole MotorControl object; never opens a serial port.

    No CLI/HTTP path constructs this. allow_motion defaults false. It must not be
    enabled before the service-level ownership/physical commissioning step.
    """
    def __init__(self,controller,*,allow_motion=False):
        if type(allow_motion) is not bool:raise ValueError('explicit_motion_policy_required')
        self.controller=controller;self.hardware_enabled=allow_motion;self.session=None;self.seq=0
    def drive(self,direction,speed):
        if not self.hardware_enabled:raise RuntimeError('physical_motion_not_commissioned')
        if self.session is None:
            self.session=self.controller.command({'action':'arm'})['session'];self.seq=0
        self.seq+=1
        self.controller.command(dict(action='drive',session=self.session,seq=self.seq,direction=direction,speed=speed))
    def hold(self):
        if self.session:
            self.seq+=1
            self.controller.command(dict(action='release',session=self.session,seq=self.seq))
    def close(self):
        # Do not globally stop an unrelated manual owner when this adapter never armed.
        if self.session:
            self.controller.command({'action':'stop'});self.session=None


class PassiveCapture:
    """Nonblocking next-result capture, serialized by MissionRuntime.tick."""
    def __init__(self,log,station,snapshot,*,now,barrier_clock):
        self.log=log
        self.event=log.begin(Station(station.station_id,station.x_m,station.y_m,station.height_m,'Automatic task association; independently supplied stationary observation required'))
        self.started=now;self.result=None
        self.uptime=_health(snapshot,log.now(),log.motion_policy)
        dht=snapshot['sensors']['dht11']
        self.marker=max(self.uptime,number(dht.get('monotonic',0),low=0),barrier_clock())
        self.counts=tuple(number(dht.get(k,0),low=0) for k in ('successes','failures'))

    def cancel(self,reason):
        if self.log.records[self.event]['Status']=='pending':
            self.log.finish(self.event,'timeout' if reason=='capture_timeout' else 'cancelled',explanation=reason)
        self.result=self.log.records[self.event]['Status']
        return self.result

    def poll(self,snapshot,*,now,timeout):
        if self.result:return self.result
        source=None
        try:
            if now-self.started>=timeout:
                status='timeout';explanation='No next sample before mission capture deadline';value=None
            else:
                uptime=_health(snapshot,self.log.now(),self.log.motion_policy)
                if uptime<self.uptime:raise ValueError('Board reboot during capture')
                source=snapshot['sensors']['dht11']
                if any(number(source.get(k,0),low=0)<old for k,old in zip(('successes','failures'),self.counts)):
                    raise ValueError('Sampler reset during capture')
                mono=number(source.get('monotonic',0),low=0)
                if mono<=self.marker or source.get('status') not in ('live','error'):return None
                sampled=datetime.fromisoformat(source['sampled_at'])
                if sampled.tzinfo is None or abs((sampled-self.log.now()).total_seconds())>9:
                    raise ValueError('Stale or invalid result timestamp')
                status='sensor_failure';value=None;explanation=str(source.get('error') or 'Invalid current DHT result')
                if source['status']=='live':
                    try:
                        value=source['value'];number(value['temperature_c'],-20,80);number(value['humidity_pct'],0,100)
                        number(source['age_seconds'],0,9)
                    except (KeyError,TypeError,ValueError):value=None
                    else:status='success';explanation='Next published outcome; task station association, not native hardware-triggered'
            self.log.finish(self.event,status,value=value,explanation=explanation,source=source)
        except (OSError,ValueError,TypeError,KeyError) as exc:
            # Persistence errors propagate; they must never masquerade as recorded failures.
            from experiment_log import LogPersistenceError
            if isinstance(exc,LogPersistenceError):raise
            self.log.finish(self.event,'request_failure',explanation=str(exc),source=source)
        self.result=self.log.records[self.event]['Status']
        return self.result


class MissionRuntime:
    def __init__(self,plan,log,motion,guidance,*,speed,clock):
        if type(speed) is not int or not 80<=speed<=220:raise ValueError('invalid_mission_speed')
        if log.purpose!='bench_diagnostic' and not motion.hardware_enabled:
            raise ValueError('simulation_must_use_bench_diagnostic')
        if log.records or log.closed:raise ValueError('new_empty_run_required')
        if log.association_mode!='automatic_stationary':raise ValueError('automatic_association_log_required')
        self.planner=MissionPlanner(plan);self.log=log;self.motion=motion;self.guidance=guidance
        self.speed=speed;self.clock=clock;self.capture=None;self.pending_ack=False;self.result_ack=None
        self.fault=None;self.closed=False;self.last_state=None
        # Exclusive on-disk reservation prevents reusing this mission ID under a log root.
        registry=log.directory.parent/'mission_ids';registry.mkdir(exist_ok=True)
        from mission_plan import validate_plan
        validate_plan(plan)
        with (registry/(plan.mission_id+'.json')).open('x') as f:
            json.dump({'run_id':log.run_id,'resume_allowed':False},f);f.flush();os.fsync(f.fileno())
        self._persist()

    def snapshot(self):
        return {**self.planner.snapshot(),'integrated':True,'integration_scope':'injected_loop_and_durable_recorder',
                'executes_hardware':self.motion.hardware_enabled,'writes_records':True,'run_id':self.log.run_id,
                'fault':self.fault,'closed':self.closed,'resume_allowed':False,
                'stationarity_source':'external_independent_input; never inferred from zero_command'}

    def _persist(self):
        state=self.snapshot()
        if state==self.last_state:return
        fd,name=tempfile.mkstemp(prefix='.mission-',dir=self.log.directory)
        try:
            with os.fdopen(fd,'w') as f:
                json.dump(state,f,ensure_ascii=False,indent=2,allow_nan=False);f.flush();os.fsync(f.fileno())
            os.replace(name,self.log.directory/'mission_state.json')
            self.last_state=state
        finally:
            if Path(name).exists():Path(name).unlink()

    def _apply(self,intents,observation,snapshot,now):
        for intent in intents:
            if intent.kind=='hold_stop':self.motion.hold()
            elif intent.kind=='follow_line':
                guide=self.guidance.classify(observation.line_values)
                if guide.kind=='line':self.motion.drive(guide.direction,self.speed)
                elif guide.kind=='crossing' and intent.reason=='leave_completed_marker':self.motion.drive('forward',self.speed)
                elif guide.kind=='crossing':self.motion.hold()  # Stop while marker debounce settles.
                else:raise RuntimeError('line_not_trackable')
            elif intent.kind=='request_capture':
                station=self.planner.plan.stations[self.planner.index]
                self.capture=PassiveCapture(self.log,station,snapshot,now=now,barrier_clock=self.clock)
                self.pending_ack=True
            elif intent.kind=='cancel_capture' and self.capture:
                self.capture.cancel(intent.reason or 'Mission cancelled')

    def tick(self,observation,snapshot,*,now):
        if self.closed:return self.snapshot()
        try:
            machine=self.planner
            if machine.state=='idle':intents=machine.start(observation,now=now)
            elif self.pending_ack and machine.state=='awaiting_pending':
                intents=machine.confirm_pending(machine.snapshot()['request_id'],self.capture.event,observation,now=now,durable=True)
                self.pending_ack=False
            elif self.result_ack and machine.state=='awaiting_result':
                intents=machine.accept_result(machine.snapshot()['request_id'],self.capture.event,machine.snapshot()['station_id'],self.result_ack,observation,now=now,durable=True)
                self.result_ack=None
            else:intents=machine.advance(observation,now=now)
            self._apply(intents,observation,snapshot,now)
            if machine.state=='awaiting_result' and not self.result_ack:
                self.result_ack=self.capture.poll(snapshot,now=now,timeout=machine.plan.timing.capture_timeout)
            if machine.state in TERMINAL:self._finish()
            self._persist()
        except Exception as exc:
            self.fault=type(exc).__name__+': '+str(exc)
            try:self._apply(self.planner.cancel(),observation,snapshot,now)
            finally:
                try:self._finish()
                finally:self._persist()
        return self.snapshot()

    def _finish(self):
        try:self.motion.close()
        finally:
            self.log.close();self.closed=True

    def cancel(self,emergency=False):
        if self.closed:return self.snapshot()
        intents=self.planner.emergency_stop() if emergency else self.planner.cancel()
        try:
            for intent in intents:
                if intent.kind=='hold_stop':self.motion.hold()
                elif intent.kind=='cancel_capture' and self.capture:self.capture.cancel(intent.reason)
        finally:
            self._finish();self._persist()
        return self.snapshot()


def inspect_run(directory):
    """Read-only restart inspection. There is deliberately no resume constructor."""
    root=Path(directory)
    state=json.loads((root/'mission_state.json').read_text())
    manifest=json.loads((root/'run_manifest.json').read_text())
    return {**state,'resume_allowed':False,'interrupted':not state.get('closed') or not manifest.get('closed_at')}


class MissionLoop:
    """Explicitly invoked loop around existing owners; no HTTP route or autostart.

    stationary_provider(now) must supply a separately measured fresh signal with
    {confirmed, sampled_at, source_id}. Zero motor targets are NOT such a signal.
    The operator must commission that source before physical operation. The loop
    uses the controller's existing independent lease watchdog; it does not replace it.
    """
    def __init__(self,runtime,fetch_snapshot,stationary_provider,markers,*,clock,wait,period,stationary_max_age):
        import math
        if not callable(stationary_provider):raise ValueError('independent_stationarity_provider_required')
        if any(type(x) not in (int,float) or not math.isfinite(x) or x<=0 for x in (period,stationary_max_age)):
            raise ValueError('explicit_loop_timing_required')
        if period>=runtime.planner.plan.timing.max_update_gap:raise ValueError('loop_slower_than_plan')
        self.runtime=runtime;self.fetch=fetch_snapshot;self.stationary=stationary_provider;self.markers=markers
        self.clock=clock;self.wait=wait;self.period=period;self.max_age=stationary_max_age
        self.sequence=0;self.last_sample=None

    def step(self):
        from mission_plan import Observation
        if self.runtime.closed:return self.runtime.snapshot()
        try:
            snapshot=self.fetch();now=self.clock()
            line=snapshot['sensors']['line_sensors'];control=snapshot['control'];host=snapshot['host']
            sampled=number(line['monotonic'],low=0)
            number(line['age_seconds'],low=0,high=self.runtime.planner.plan.timing.max_observation_age)
            if line['status']!='live':raise ValueError('line_sensor_not_live')
            if self.last_sample is not None and sampled<=self.last_sample:raise ValueError('line_sample_replayed')
            raw=tuple(line['value']);guide=self.runtime.guidance.classify(raw)
            marker=self.markers.update(guide.kind=='crossing',sampled)
            stationary=self.stationary(now)
            now=self.clock()  # Providers may consume time; validate against receipt time.
            if (not isinstance(stationary,dict) or type(stationary.get('confirmed')) is not bool or
                    not isinstance(stationary.get('source_id'),str) or not stationary['source_id'].strip() or
                    stationary['source_id'] in ('zero_command','motor_command','timer')):
                raise ValueError('independent_stationarity_evidence_required')
            age=now-number(stationary.get('sampled_at'),low=0)
            if not 0<=age<self.max_age:raise ValueError('stationarity_signal_stale')
            outputs=control.get('outputs')
            zero=isinstance(outputs,list) and len(outputs)==4 and all(type(v) is int and v==0 for v in outputs)
            healthy=control.get('enabled') is True and control.get('fault') is None and control.get('state') in ('stopped','armed','driving')
            self.sequence+=1;self.last_sample=sampled
            observation=Observation(sampled,self.sequence,host['hostname'],raw,tuple(line['physical_pins']),
                                    guide.kind in ('line','crossing'),zero,stationary['confirmed'],healthy,False,marker)
            return self.runtime.tick(observation,snapshot,now=now)
        except Exception as exc:
            self.runtime.fault=type(exc).__name__+': '+str(exc)
            self.runtime.cancel()
            return self.runtime.snapshot()

    def run(self,cancelled):
        """cancelled is an Event; already-set cancellation cannot cause first drive."""
        try:
            while not cancelled.is_set() and not self.runtime.closed:
                self.step()
                if not self.runtime.closed:self.wait(self.period)
        finally:
            if not self.runtime.closed:self.runtime.cancel()
        return self.runtime.snapshot()
