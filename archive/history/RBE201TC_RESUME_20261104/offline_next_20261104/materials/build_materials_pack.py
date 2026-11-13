"""Build only material-management CSVs inside the assigned materials scope.
No hardware, service, report answers, individual LCA or assessed-video content.
"""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import csv
import hashlib
import json
import tarfile
import zipfile

ROOT = Path('/Users/your-user/Documents/Codex/2026-09-18/di').resolve()
OUT = ROOT / 'outputs/RBE201TC_RESUME_20261104/offline_next_20261104/materials'
STAMP = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
PREFIX = 'outputs/RBE201TC_RESUME_20261104/'

def link(path):
    path = Path(path)
    return f'[{path.name}](<{path.as_posix()}>)'

def output(name, headers, rows):
    path = OUT / name
    assert path.resolve().is_relative_to(OUT.resolve())
    assert not path.exists(), f'Refusing unobserved overwrite: {path}'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows(rows)

# Each populated row is either an existing file or a clearly labelled not-provided category.
specs = [
('资料管理','原课程要求整理',PREFIX.replace('RBE201TC_RESUME_20261104/','RBE201TC_HANDOFF_20261104/')+'COURSE_REQUIREMENTS.md','D0','既有审计依据；非新课程公告','本次核读关联审计；课程平台未重验'),
('资料管理','原审计差距矩阵',PREFIX+'course_audit/COURSE_EVIDENCE_GAP_CHECKLIST_ZH.md','D0','已完成的课程审计，不在本轮重写','本次核读相关段落'),
('资料管理','原来源核查',PREFIX+'course_audit/SOURCE_CHECKS.md','D0','既有DOCX/归档/bench核查记录','本轮引用原审计，不重填课堂练习'),
('资料管理','最新继续入口',PREFIX+'CONTINUE_HERE.md','D0','下午交付状态入口；不是实时在线探测','本次核读'),
('软件说明','采集与部署回退说明','work/device-console-v2/DATA_COLLECTION.md','D0','新网页增量未部署生产，人工停稳观察','本次核读；不执行部署'),
('软件验证','网页采集完整QA',PREFIX+'web_acquisition_qa/QA_REPORT.md','S1','隔离98后端/65前端/Chromium记录；非场地','本次核读QA，未重跑'),
('软件验证','隔离后端最终日志',PREFIX+'web_acquisition_qa/rdk-backend-tests-rerun.log','S1','Ran 98 tests; OK; isolated exit=0','本次核读日志末段'),
('软件验证','前端最终日志',PREFIX+'web_acquisition_qa/frontend-tests.log','S1','tests 65 / pass 65 / fail 0','本次核读日志及汇总'),
('软件验证','Chromium夹具结果',PREFIX+'web_acquisition_qa/browser-summary.json','S1','real_hardware_used=false，软件CSV/界面流程','本次核读JSON；非实物测量'),
('软件版本','源码校验及部署状态',PREFIX+'web_acquisition_qa/source-integrity.json','D0','生产未部署；校验仅对应原检查时点','本次核读；不重连设备'),
('软件版本','板上只读边界快照',PREFIX+'web_acquisition_qa/rdk-source-boundary.json','D0','2026-11-04 06:39:40Z时生产无新模块','本次核读历史快照，非当前在线保证'),
('实物记录','用户控制/探头观察记录','work/device-console-v2/config/hardware_observations.json','B2','用户确认方向/控制恢复；黑底与顺序未核验','本次核读；不重复实物测试'),
('实物记录','白纸22样本原始记录',PREFIX+'line_white_reference_20261104T114543_45c11c.json','B2','四路白底0；不能证明黑底极性','既有审计已逐项核对；本轮仅存在性/哈希'),
('实物bench','11:24 CSV',PREFIX+'bench_diagnostics/20261104T032454112979Z_ad143e54/temperature_points_20261104T032454112979Z_ad143e54.csv','B2','1 success，无坐标；非正式场地','既有审计内容核对；本轮重新解析'),
('实物bench','11:24原运行清单',PREFIX+'bench_diagnostics/20261104T032454112979Z_ad143e54/run_manifest.json','B2','原清单没有raw_csv_sha256/row_count','既有审计内容核对；本轮重新解析'),
('实物bench','11:37 CSV',PREFIX+'bench_diagnostics/20261104T033722985982Z_75a143bf/temperature_points_20261104T033722985982Z_75a143bf.csv','B2','1 request_failure，温湿度空，无坐标','既有审计内容核对；本轮重新解析'),
('实物bench','11:37原运行清单',PREFIX+'bench_diagnostics/20261104T033722985982Z_75a143bf/run_manifest.json','B2','存在正常关闭CSV哈希及行数','本轮重新解析/匹配原CSV'),
('实物bench','13:21 CSV',PREFIX+'bench_diagnostics/20261104T052126093980Z_1201f6a7/temperature_points_20261104T052126093980Z_1201f6a7.csv','B2','10条:9 success+1 sensor_failure，无坐标','既有审计内容核对；本轮重新解析'),
('实物bench','13:21原运行清单',PREFIX+'bench_diagnostics/20261104T052126093980Z_1201f6a7/run_manifest.json','B2','存在正常关闭CSV哈希及行数','本轮重新解析/匹配原CSV'),
('场地准备','真实站点空表','work/device-console-v2/config/stations.csv','D0','仅表头，0个实际站点；不填写坐标','本轮重新解析'),
('MATLAB','已完成静态QA记录',PREFIX+'matlab_qa/QA_REPORT.md','D0','该记录时32测试已写/0执行；task-6另核','本次核读记录；不冒称最新授权运行结果'),
('MATLAB','静态检查原日志',PREFIX+'matlab_qa/mh_lint.log','D0','3个M文件MISS_HIT exit=0，不等于执行','本次核读'),
('MATLAB','地图脚本','work/device-console-v2/matlab/map_room.m','D0','存在；本轮未执行，不是成品地图','仅存在性/哈希；算法以QA记录为准'),
('MATLAB','测试执行入口','work/device-console-v2/matlab/run_offline_tests.m','D0','存在；实际执行结果由task-6另行记录','仅存在性/哈希，未执行'),
('MATLAB','32项测试源','work/device-console-v2/matlab/tests/TestMapRoom.m','D0','软件夹具测试源，不是正式房间数据','仅存在性/哈希，未执行'),
('原课程附件','生活碳排放课堂空表','outputs/RBE201TC_HANDOFF_20261104/original_project_20261102/RBE201TC_Annual_Carbon_Emissions_Student_Worksheet.docx','D0','课堂生活排放练习，不是机器人个人LCA','本轮未重读Office；结论引用原审计'),
('设备过程图','历史设备HEIC','outputs/RBE201TC_HANDOFF_20261104/reference/IMG_9535.HEIC','D0','历史照片存在；不是最新CAD/制造验收','仅存在性/哈希，未重新目视'),
('离线打包','下午离线结果归档',PREFIX+'RBE201TC_OFFLINE_RESULTS_20261104.zip','D0','既有离线交付包，不是最终课程ZIP','本轮只查成员名称，不执行/全CRC验收'),
('MATLAB环境','本轮环境核查报告',PREFIX+'offline_next_20261104/matlab/REPORT.md','D0','完成搜索范围内无入口；授权未验证；0执行/32未执行','本次核读同伴最终报告；权限/超时限制保留'),
('MATLAB环境','本轮结构化执行状态',PREFIX+'offline_next_20261104/matlab/execution-status.json','D0','MATLAB未调用/版本未知/退出码不适用，静态单独通过','本次核读结构化状态，不把0执行当通过'),
('MATLAB环境','本轮32项未执行清单',PREFIX+'offline_next_20261104/matlab/test-inventory.txt','D0','源码静态扫描清单，32项NOT EXECUTED','本次核读，不是MATLAB发现/执行结果'),
]
index=[]
for i,(category,title,relative,level,state,checked) in enumerate(specs,1):
    path=ROOT/relative
    assert path.is_file(), relative
    data=path.read_bytes()
    index.append([f'M{i:03d}',category,title,link(relative),'存在',level,state,checked,'',len(data),hashlib.sha256(data).hexdigest(),STAMP,''])
missing = [('CAD/制造','SolidWorks原生零件、装配、图纸、制造导出与依赖'),('BOM/质量','项目BOM、实际材料牌号/采购来源/实测质量'),('制造','打印/加工设置、制造时间、用量/废料、装配实测'),('能耗','任务定义、实际仪器、原始电压/电流/能量记录'),('教师模板','指定LaTeX模板及附件、课程最新公告'),('贡献','学生核实的贡献声明与自评互评表'),('正式场地','房间/站点/高度、正式CSV、路线/干预记录'),('正式分析','真实房间MATLAB执行日志与温湿度两图'),('评估成品','学生独立报告及个人机器人LCA'),('学生视频','学生自行制作的MP4；录制PPT时其PDF')]
for i,(category,title) in enumerate(missing,101):
    index.append([f'M{i}',category,title,'','本次提供材料未见','','本次未见不代表组员未完成','仅当前工作区glob及归档名称；未查外部平台','', '', '',STAMP,'由学生提供真实文件后再登记，勿填示例或替代品'])
output('MATERIALS_INDEX.csv',['MaterialID','Category','Title','SourceLink','PresenceState','EvidenceLevel','SupportedState','ContentCheck','OwnerStudentSupplied','BytesAtCheck','SHA256AtCheck','CheckedAtWithTZ','NextActionOrNotes'],index)

TEMPLATES = {
'BOM_EMPTY.csv':['ItemID','AssemblyReference','ComponentName','MaterialOrGrade','Quantity','QuantityUnit','SupplyOrReuseSource','SupplierPartNumber','CADRevision','MassMeasurementID','BatteryLabelRecordID','OriginFileLink','ProvidedByStudent','CheckedByStudent','CheckedAtWithTZ','UnknownOrNotes'],
'CAD_DEPENDENCIES_EMPTY.csv':['CADFileID','OriginalFileLink','FileType','CADSoftwareVersion','Revision','AssemblyParentID','RequiredDependencyFileLink','ExternalReferenceStatus','Units','ManufacturingExportLink','PhysicalPartID','OwnerStudentSupplied','OpenedWithVersion','OpenCheckAtWithTZ','OpenCheckResult','UnknownOrNotes'],
'MANUFACTURING_EMPTY.csv':['ManufacturingID','PhysicalPartID','CADFileID','CADRevision','Process','MaterialOrGrade','MachineOrPrinter','SlicerOrProcessSettingsLink','EstimatedTime_min','ActualTime_min','InputMaterialMass_g','FinishedPartMass_g','WasteMass_g','ValueClassMeasuredOrEstimated','InstrumentOrSourceLink','MadeAtWithTZ','RawEvidenceLink','RecordedByStudent','UnknownOrNotes'],
'MASS_MEASUREMENTS_EMPTY.csv':['MeasurementID','ItemOrAssemblyID','MeasurementScope','Mass_g','InstrumentID','InstrumentResolution_g','InstrumentReferenceOrCalibrationLink','MeasuredAtWithTZ','RawEvidenceLink','MeasuredByStudent','UncertaintyOrUnknown','Notes'],
'BATTERY_LABEL_EMPTY.csv':['BatteryRecordID','PhysicalBatteryID','Manufacturer','Model','LabelNominalVoltage_V','LabelCapacityValue','LabelCapacityUnit','ChemistryAsLabelled','LabelPhotoLink','LabelTranscribedByStudent','CheckedAtWithTZ','UnknownOrNotes'],
'ENERGY_RUNS_EMPTY.csv':['EnergyRunID','TaskDefinition','RouteOrLoadDefinitionLink','HardwareRevision','SoftwareVersionLink','BatteryRecordID','MeasurementBoundary','InstrumentID','InstrumentReferenceOrCalibrationLink','StartedAtWithTZ','EndedAtWithTZ','ActualTaskResult','SuccessCriteriaLink','SampleFileLink','MeasuredEnergy_Wh','EnergyDataClassMeasuredOrCalculated','CalculationMethodLink','Interventions','RecordedByStudent','UnknownOrNotes'],
'ENERGY_SAMPLES_EMPTY.csv':['EnergyRunID','SampleNumber','TimestampWithTZ','Elapsed_s','Voltage_V','Current_A','Power_W','AccumulatedEnergy_Wh','ChannelAndMeasurementBoundary','DataClassMeasuredOrCalculated','InstrumentID','RawInstrumentFileLink','MissingOrFailureReason','Notes'],
'LCA_SOURCE_FACTS_EMPTY.csv':['FactID','LifecycleStage','ItemOrProcessID','FactName','FactValue','Unit','DataClassMeasuredEstimatedOrLiterature','SystemBoundaryOrFunctionalUnitAsStudentDefined','OriginalSourceLink','SourceAuthorOrOrganisation','SourceDateOrVersion','AccessedAtWithTZ','ApplicableRegionOrProcess','UncertaintyOrUnknown','CollectedByStudent','Notes'],
'STUDENT_CONTRIBUTION_EMPTY.csv':['ContributionID','FullNameStudentSupplied','StudentIDStudentSupplied','RoleStudentSupplied','SpecificCompletedTask','IndividualOrShared','ArtifactLink','ArtifactVersionOrSHA256','TestOrMeetingEvidenceLink','ContributionTimeRange','ConfirmedByStudent','ConfirmedAtWithTZ','UnknownOrNotes'],
'FUNCTION_CLAIMS_EMPTY.csv':['FunctionID','GroupDecisionClaimOrNotClaim','ClaimNameStudentDefined','BaselineOrExtensionArea','DistinctFunctionRationaleStudentDefined','RegisteredMemberCount','AcceptanceCriteriaStudentDefined','SoftwareEvidenceLink','PhysicalBenchEvidenceLink','FormalFieldEvidenceLink','CurrentEvidenceLevel','UnverifiedLimits','ResponsibleStudent','DecisionAtWithTZ','Notes'],
'SOURCE_RECEIPT_EMPTY.csv':['ReceiptID','MaterialID','ProvidedByStudent','ReceivedAtWithTZ','OriginalFileLink','OriginAndVersion','PermissionOrOwnershipConfirmation','EvidenceClass','PresenceCheck','ContentCheckByStudent','ContentCheckAtWithTZ','UnknownOrNotes'],
'ARCHIVE_MANIFEST_EMPTY.csv':['ArchiveEntryID','OriginalFileLink','PackageRelativePath','Role','SourceOrDerived','EvidenceClass','Bytes','SHA256','ReferencedInputsLink','RequiredSoftwareAndVersion','PersonalDataOrSecretReview','OwnerStudentSupplied','CheckedAtWithTZ','CheckResult','Notes'],
'REPRODUCTION_RUN_EMPTY.csv':['ReproductionID','ScopeSoftwareFixtureOrRealData','PackageVersionOrManifestLink','EnvironmentAndVersion','InputFileLinks','InputSHA256','CommandOrProcedureReference','StartedAtWithTZ','EndedAtWithTZ','ExitCode','TestCountExecuted','PassedCount','FailedOrIncompleteCount','OutputFileLinks','OutputHashesLink','RunnerStudentSupplied','UnverifiedLimits','Notes'],
'AI_ASSISTANCE_REGISTER_EMPTY.csv':['AssistanceID','ToolAndVersion','UsedAtWithTZ','PermittedPurpose','AffectedArtifactLinks','AffectedPartOrChange','HumanVerificationAndSources','IndependentAuthorStudentSupplied','CourseRuleCheck','DisclosureLocationStudentChosen','Notes'],
}
for name,headers in TEMPLATES.items():output('templates/'+name,headers,[])

web=PREFIX+'web_acquisition_qa/QA_REPORT.md'
mat=PREFIX+'matlab_qa/QA_REPORT.md'
hw='work/device-console-v2/config/hardware_observations.json'
bench=PREFIX+'bench_diagnostics/20261104T052126093980Z_1201f6a7/temperature_points_20261104T052126093980Z_1201f6a7.csv'
functions=[
('F01','人工按键控制与停车链路','已有基础；不代小组申报','待小组确认',link(web),'S1：FakeSerial与Chromium控制回归已记载',link(hw),'B2：用户确认方向/控制恢复；不是编码器精度','','本次未见F3','实际轮速/角点逐项映射/停车距离未由上述证明','仅补申报所需真实证据，不重复已确认方向',''),
('F02','自动黑胶带循迹与房间路线','课程基线','待小组确认','','无已集成路线通过记录',link(hw),'B2：仅白底/控制反馈，黑底与探头顺序未核验','','本次未见F3','老师程序存在或纯软件任务准备不等于集成循迹通过','返场前只整理接口/判据；当前不执行现场',''),
('F03','温湿度记录/网页持久CSV','基线采集途径；网页传输可作为扩展候选','待小组确认',link(web),'S1：98后端/65前端/Chromium；生产未部署',link(bench),'B2：历史实读记录均bench/无坐标；不等同新网页部署','','本次未见F3','实物精度/自动定位/完整房间覆盖；软件夹具不算实物','保留部署与用途分隔，正式场地数据另收',''),
('F04','MATLAB温度与相对湿度双地图','课程基线','待小组确认',link(PREFIX+'offline_next_20261104/matlab/REPORT.md'),'D0：本轮完成搜索范围内无入口；0执行/32未执行；静态单独通过；权限/超时限制保留','','无足够定位实物数据','','本次未见F3','未提供真实数据上的MATLAB执行与双图；静态不等于运行','未来可用授权环境再执行；仍需真实场地输入',''),
('F05','自制底盘/连接件与结构实现','课程CAD/制造学习成果','待小组确认','','本次未见项目CAD/制造验证','','本次未见对应制造/质量证据','','本次未见F3','不能以原设备照片或硬件手册冒充设计制造','让组员提供原生CAD/依赖/制造实物对应表',''),
('F06','轻量化/材料利用/能耗改善','可选扩展候选，非全部必做','待小组确认','','本次未见项目模型/对照数据','','本次未见实际质量/任务能耗记录','','本次未见F3','11.7V查询不证明Wh、容量或续航；无个人LCA答案','先收事实与来源，只填空模板，不代写评估',''),
('F07','视觉循迹/避障','可选扩展候选，非摄像头预览','待小组确认','','本次未见实现/通过记录','','历史相机在线不等于视觉导航','','本次未见F3','不把预览/UI美化自动计成独立功能','若小组确实申报，再收独立实现和验收证据',''),
('F08','房间底图/露点/舒适指标等地图增强','可选扩展候选，基础双图不重复算','待小组确认','','本次未见该独立扩展的运行证据','','本次未见','','本次未见F3','不能用未测数值/模型输出冒充观测','先由学生确认选项与数据/引用需求，不造图',''),
]
output('FUNCTION_EVIDENCE_MATRIX.csv',['FunctionID','FunctionName','CourseRole','GroupDeclarationStatus','SoftwareEvidenceLink','SoftwareValidationState','PhysicalBenchEvidenceLink','PhysicalBenchValidationState','FormalFieldEvidenceLink','FormalFieldValidationState','NotProven','NextEvidence','OwnerStudentSupplied'],functions)

# Update only filename/structure discovery; do not reread or solve coursework attachments.
lines=['# 本轮新材料发现记录','',f'检查时间：{STAMP}。','',
       '本轮只更新文件存在性与材料管理，不重写原课程审计；未操作硬件或网络服务，未重新读取Office内容。', '',
       '## 工作区glob结果','',
       '- CAD/制造常见格式（SolidWorks、STL、STEP、IGES、DXF/DWG、3MF/G-code及常见大写）本轮未见。',
       '- 本项目指定LaTeX模板/源、报告、自评互评、视频、项目BOM/制造/能耗成品本轮未见；只有既有课程附件/课堂练习、软件及其QA。',
       '- 该结论只覆盖可读工作区；不代表组员在其他设备或云盘未完成。',
       '- 新发现下午离线结果ZIP，只检查成员名，不能把它当课程最终提交。','',
       '## ZIP/TGZ成员名复核','']
archives=sorted(p for p in ROOT.rglob('*') if p.is_file() and p.suffix.lower() in {'.zip','.tgz'})
suffixes={'.sldprt','.sldasm','.slddrw','.stl','.step','.stp','.iges','.igs','.dxf','.dwg','.3mf','.gcode','.tex','.bib','.cls','.sty','.mp4','.mov','.mkv','.avi','.m4v','.webm'}
for path in archives:
    if path.suffix.lower()=='.zip':
        with zipfile.ZipFile(path) as z:names=[i.filename for i in z.infolist() if not i.is_dir()]
    else:
        with tarfile.open(path,'r:gz') as t:names=[i.name for i in t.getmembers() if i.isfile()]
    matches=[n for n in names if Path(n).suffix.lower() in suffixes]
    lines += [f'- {link(path.relative_to(ROOT))}：{len(names)}个文件成员；常见CAD/LaTeX/视频格式匹配{len(matches)}个。']
    if matches:lines += ['```text',*matches,'```']
lines += ['', '归档成员未解包、未执行、未全量CRC验收；匹配0不排除非常规文件名或外部资料。','',
          '## bench与站点实际复核','']
for path in sorted((ROOT/PREFIX/'bench_diagnostics').glob('*/temperature_points_*.csv')):
    with path.open(encoding='utf-8-sig',newline='') as stream:rows=list(csv.DictReader(stream))
    manifest=path.with_name('run_manifest.json');m=json.loads(manifest.read_text())
    assert all(r['RunPurpose']=='bench_diagnostic' for r in rows)
    assert all(not r['X_m'] and not r['Y_m'] and not r['SensorHeight_m'] for r in rows)
    assert all(not r['Temperature_°C'] and not r['Humidity_%RH'] for r in rows if r['Status']!='success')
    status={s:sum(r['Status']==s for r in rows) for s in sorted({r['Status'] for r in rows})}
    if 'raw_csv_sha256' in m:
        assert m['raw_csv_sha256']==hashlib.sha256(path.read_bytes()).hexdigest() and m['row_count']==len(rows)
        verification='与原清单SHA/行数一致'
    else:verification='原清单未提供CSV哈希/行数，不能作历史完整性已验证声明'
    lines += [f'- {link(path.relative_to(ROOT))}：{len(rows)}行，状态`{status}`；无坐标；{verification}。']
station=ROOT/'work/device-console-v2/config/stations.csv'
with station.open(encoding='utf-8-sig',newline='') as stream:assert len(list(csv.DictReader(stream)))==0
lines += [f'- {link(station.relative_to(ROOT))}：0条数据行，仅表头。','',
          '上述实物bench不是F3正式场地数据，也不构成房间地图或机器人任务成功率；本轮不新采数据。','']
p=OUT/'MATERIAL_DISCOVERY_UPDATE.md'
assert not p.exists();p.write_text('\n'.join(lines),encoding='utf-8')
print(f'Built index={len(index)} entries; header-only templates={len(TEMPLATES)}; function rows={len(functions)}; archives={len(archives)}')
