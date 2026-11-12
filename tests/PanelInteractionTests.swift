// Runs against the actual panel state machine, without opening a window or querying user data.
var assertions=0
func expect(_ condition:@autoclosure ()->Bool,_ message:String){assertions+=1;if !condition(){fatalError(message)}}
let fixture="""
{"dashboard":{"date":"","dates":[],"providers":[],"note":"","rangeKey":"today","rangeLabel":"当天","chartUnit":"","chartSpan":"","chartStatus":"","allowCollapse":true,"expandedProviders":[]},"entries":[],"moduleOptions":[{"label":"允许折叠","checked":true,"action":["set-collapse","false"],"children":[]}],"preferences":{"revision":1,"visible":["summary","opencodex","claude"],"appearance":"glass","allowCollapse":true,"expandedProviders":[]}}
"""
let initial=try JSONDecoder().decode(Snapshot.self,from:Data(fixture.utf8))
func saved(_ revision:Int64,_ expanded:[String])->PresentationPreferences{PresentationPreferences(revision:revision,visible:["summary","opencodex","claude"],appearance:"glass",allowCollapse:true,expandedProviders:expanded)}
func expanded(_ state:PanelInteraction)->Set<String>{Set(state.displayed()?.dashboard?.expandedProviders ?? [])}
var state=PanelInteraction(source:initial)
expect(state.submit(["set-expanded","opencodex","true"]) == .start,"First click must start")
expect(state.submit(["set-expanded","claude","true"]) == .queued,"Second module must queue")
expect(expanded(state)==Set(["opencodex","claude"]),"Both modules must expand immediately")
state.finish(saved(2,["opencodex"]))
state.source=initial // The old data render arrives after the save receipt.
expect(expanded(state)==Set(["opencodex","claude"]),"A stale render must not revert saved or queued state")
expect(state.next()==["set-expanded","claude","true"],"Queue must retain the next module")
expect(expanded(state)==Set(["opencodex","claude"]),"Starting queued action must not close the first module")
state.finish(saved(3,["opencodex","claude"]))
expect(expanded(state)==Set(["opencodex","claude"]),"Saved multiple expansions survive stale data")
expect(state.submit(["set-expanded","opencodex","false"]) == .start,"Close one module")
expect(expanded(state)==Set(["claude"]),"Closing one module must not close the other")
expect(state.submit(["set-expanded","opencodex","true"]) == .queued,"Rapid reopen queues")
expect(state.submit(["set-expanded","opencodex","false"]) == .queued,"Rapid reclose queues")
expect(expanded(state)==Set(["claude"]),"Newest click wins optimistically")
state.finish(saved(4,["claude"]))
_ = state.next() // Older reopen is now active, newer reclose remains queued.
expect(expanded(state)==Set(["claude"]),"Dispatching an older click must not override a newer queued click")
state.finish(saved(5,["opencodex","claude"]))
_ = state.next()
expect(expanded(state)==Set(["claude"]),"Last queued close still wins after the previous save")
state.finish(saved(6,["claude"]))
expect(state.next()==nil,"Queue drains")
var newer=initial;newer.preferences=saved(7,["opencodex"]);state.source=newer
expect(expanded(state)==Set(["opencodex"]),"Newer external settings override an older acknowledged state")
var busy=PanelInteraction(source:initial)
expect(busy.submit(["refresh-source","komari"]) == .start,"Resource check starts")
expect(busy.submit(["set-range","7d"]) == .queued,"Range click must not disappear during resource check")
expect(busy.submit(["refresh-view"]) == .ignored,"An automatic check cannot queue ahead of user actions")
expect(PanelInteraction.swiftBarName("/Users/a/软件:配置/swiftbar/token-monitor.30s.sh")=="token-monitor","Panel wakes SwiftBar by the plugin name every version matches")
expect(PanelInteraction.mayExpire(["refresh-source","komari"],claimed:true),"A lost automatic receipt cannot stall the panel")
expect(PanelInteraction.mayExpire(["reauthorize-claude-oauth"],claimed:false),"An unclaimed authorization click can time out")
expect(!PanelInteraction.mayExpire(["reauthorize-claude-oauth"],claimed:true),"A visible authorization prompt can wait for a decision")
expect(busy.submit(["open-settings"]) == .queued,"Menu action must not disappear during resource check")
expect(busy.submit(["open-settings"]) == .ignored,"Repeated non-preference action is not duplicated")
expect(busy.submit(["set-expanded","claude","true"]) == .queued,"Expansion during check queues")
expect(expanded(busy)==Set(["claude"]),"Expansion still responds immediately")
busy.finish(nil);expect(busy.next()==["set-range","7d"],"Range dispatches in order")
busy.finish(nil);expect(busy.next()==["open-settings"],"Menu action dispatches in order")
busy.finish(nil);expect(busy.next()==["set-expanded","claude","true"],"Expansion is eventually saved")
busy.finish(saved(2,["claude"]));expect(busy.next()==nil,"Background queue drains")
_ = busy.submit(["set-collapse","false"])
expect(busy.displayed()?.dashboard?.allowCollapse==false,"Disable collapse immediately")
expect(expanded(busy)==Set(["claude"]),"Disable collapse preserves independent state")
let scroll=ContentScrollView(frame:NSRect(x:0,y:0,width:200,height:150))
let document=FlippedView(frame:NSRect(x:0,y:0,width:200,height:100));scroll.documentView=document;scroll.configureScrolling()
expect(!scroll.needsVerticalScrolling,"Content that fits must not scroll")
expect(scroll.verticalScrollElasticity == .none,"Content that fits must not bounce")
let wheel=CGEvent(scrollWheelEvent2Source:nil,units:.pixel,wheelCount:1,wheel1:80,wheel2:0,wheel3:0)!
scroll.scrollWheel(with:NSEvent(cgEvent:wheel)!)
expect(scroll.contentView.bounds.origin == .zero,"Two-finger scroll must leave fitting content still")
document.setFrameSize(NSSize(width:200,height:500));scroll.configureScrolling()
expect(scroll.needsVerticalScrolling,"Overflow must remain scrollable")
scroll.contentView.scroll(to:NSPoint(x:0,y:120));document.setFrameSize(NSSize(width:200,height:100));scroll.configureScrolling()
expect(scroll.contentView.bounds.origin == .zero,"Collapsing content must clear obsolete scroll offset")
expect(!scroll.hasVerticalScroller,"No scrollbar when everything fits")
let watchDirectory=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
try FileManager.default.createDirectory(at:watchDirectory,withIntermediateDirectories:true)
let observer=SnapshotObserver();var notifications=0
observer.start(directory:watchDirectory.path){notifications+=1}
expect(observer.active,"Directory observer starts without a polling timer")
try Data("first".utf8).write(to:watchDirectory.appendingPathComponent("panel-view.json"),options:.atomic)
let deadline=Date().addingTimeInterval(2)
while notifications==0 && Date()<deadline{RunLoop.main.run(until:Date().addingTimeInterval(0.02))}
expect(notifications>0,"Atomic snapshot replacement wakes the observer")
let count=notifications
RunLoop.main.run(until:Date().addingTimeInterval(0.2))
expect(notifications==count,"Idle directory produces no recurring wakeups")
try FileManager.default.removeItem(at:watchDirectory)
let removedDeadline=Date().addingTimeInterval(2)
while observer.active && Date()<removedDeadline{RunLoop.main.run(until:Date().addingTimeInterval(0.02))}
expect(!observer.active,"Removing the cache directory releases a stale watch")
try FileManager.default.createDirectory(at:watchDirectory,withIntermediateDirectories:true)
observer.start(directory:watchDirectory.path){notifications+=1}
expect(observer.active,"A recreated cache directory can be watched again")
observer.stop();expect(!observer.active,"Observer releases its file descriptor on stop")
try FileManager.default.removeItem(at:watchDirectory)
let monitor=NSRect(x:0,y:0,width:1200,height:900)
let rootFrame=NSRect(x:900,y:200,width:280,height:650)
let secondFrame=NSRect(x:671,y:100,width:224,height:616)
let thirdX=MenuLayout.flyoutX(parent:secondFrame,ancestor:rootFrame,width:260,screen:monitor)
expect(thirdX+260<secondFrame.minX,"Third level continues left instead of covering the root")
let rightRoot=NSRect(x:10,y:200,width:280,height:650),rightSecond=NSRect(x:295,y:100,width:224,height:600)
expect(MenuLayout.flyoutX(parent:rightSecond,ancestor:rightRoot,width:260,screen:monitor)>rightSecond.maxX,"Right-opening menus keep opening right")
let tight=NSRect(x:0,y:0,width:500,height:900)
let tightX=MenuLayout.flyoutX(parent:NSRect(x:20,y:0,width:224,height:600),ancestor:rootFrame,width:260,screen:tight)
expect(tightX>=4 && tightX+260<=496,"Constrained flyouts stay within the screen")
let overlap=NSRect(x:950,y:400,width:224,height:200),point=NSPoint(x:1000,y:450)
expect(MenuLayout.topmostIndex(at:point,frames:[rootFrame,overlap])==1,"Covered rows cannot own hover or mouse input")
expect(MenuLayout.topmostIndex(at:NSPoint(x:1000,y:650),frames:[rootFrame,overlap])==0,"Uncovered ancestor rows remain reachable")
expect(MenuLayout.topmostIndex(at:NSPoint(x:100,y:100),frames:[rootFrame,overlap])==nil,"Outside points do not hit a menu")
var grouped=initial.dashboard!
let sampleProvider=ProviderUsage(id:"claude",name:"Claude",subtitle:"",status:"",available:false,cost:nil,tokens:0,requests:0,output:0,cache:nil,coverage:nil,sessions:0,unpriced:0,summaryIncluded:true,models:[],history:[])
expect(DashboardView.harnessHeaderHeight(grouped)==0,"Empty Harness group has no header")
grouped.providers=[sampleProvider]
expect(DashboardView.harnessHeaderHeight(grouped)==6,"Harness rows get a small gap instead of a heading")
let groupedView=DashboardView(grouped,width:344)
let disclosure=groupedView.subviews.compactMap{$0 as? DisclosureButton}.first!
expect(disclosure.frame.minY==DashboardView.summaryHeight(grouped)+DashboardView.harnessHeaderHeight(grouped)+2,"Disclosure hitbox follows the gap above the Harness rows")
grouped.showSummary=false
expect(DashboardView.harnessHeaderHeight(grouped)==0,"Hidden Summary uses the title without a duplicate Harness header")
expect(MenuLayout.configured("已配置")==true,"Configured gets a filled indicator")
expect(MenuLayout.configured("未配置")==false,"Unconfigured gets an outline indicator")
expect(MenuLayout.configured("已同步")==nil,"Connection results must not be confused with configuration")
let configuredEntry=try JSONDecoder().decode(Entry.self,from:Data("{\"label\":\"ChatGPT\",\"value\":\"已配置\",\"symbol\":\"folder\",\"children\":[{\"label\":\"目录\",\"children\":[]}]}".utf8))
expect(MenuLayout.valueWidth(configuredEntry)==MenuLayout.width("已配置",font:MenuLayout.valueFont)+12,"Indicator reserves its own column")
expect(MenuLayout.labelWidth(configuredEntry,width:280,left:36)+36+MenuLayout.trailing(configuredEntry)+MenuLayout.valueWidth(configuredEntry)+10==280,"Status, label and arrow cannot overlap")
// Two keys with 19-character names need far more than a homepage row; the value gives way, never the title.
let longNames=try JSONDecoder().decode(Entry.self,from:Data("{\"label\":\"Kimi API\",\"value\":\"余额 一二三四五六七八九十一二三四五六七八九 ¥100.00 · 九八七六五四三二一十九八七六五四三二一 ¥99.99\",\"fallbacks\":[\"余额 ¥100.00 · ¥99.99\",\"2 把 Key\"],\"children\":[{\"label\":\"K0\",\"children\":[]}]}".utf8))
let shown=MenuLayout.fitted(longNames,width:344,left:36)
expect(shown.value=="余额 ¥100.00 · ¥99.99","Key names that don't fit give way to the bare amounts")
expect(MenuLayout.labelWidth(shown,width:344,left:36)>=MenuLayout.width("Kimi API",font:MenuLayout.labelFont(shown)),"The title keeps its full width beside the value")
expect(MenuLayout.rowHeight(longNames,width:344,left:36)==MenuLayout.rowHeight(shown,width:344,left:36),"Rows are measured with the value they show")
var many=longNames;many.fallbacks=["余额 ¥1000000.00 · ¥2000000.00 · ¥3000000.00 · ¥4000000.00","4 把 Key"]
expect(MenuLayout.fitted(many,width:344,left:36).value=="4 把 Key","Amounts that still don't fit give way to the key count")
var roomy=longNames;roomy.value="余额 默认 ¥228.37 · lab ¥-0.69"
expect(MenuLayout.fitted(roomy,width:344,left:36).value==roomy.value,"Names that fit stay listed")
let mergedEntry=try JSONDecoder().decode(Entry.self,from:Data("{\"label\":\"ChatGPT 订阅\",\"checked\":false,\"value\":\"已配置\",\"action\":[\"set-module\",\"chatgpt\",\"true\"],\"children\":[{\"label\":\"选择配置目录\",\"action\":[\"choose-client-home\",\"chatgpt\"],\"children\":[]}]}".utf8))
expect(!MenuLayout.configurationClick(mergedEntry,x:19,width:264),"Checkbox click toggles rather than opening settings")
expect(!MenuLayout.configurationClick(mergedEntry,x:110,width:264),"Label click retains multi-select behavior")
expect(MenuLayout.configurationClick(mergedEntry,x:247,width:264),"Arrow click opens settings rather than toggling")
expect(!MenuLayout.configurationHover(mergedEntry,x:19),"Hovering a checkbox must not open a flyout")
expect(MenuLayout.configurationHover(mergedEntry,x:110),"Hovering the label opens configuration")
var mergedSnapshot=initial;mergedSnapshot.moduleOptions=[mergedEntry]
var mergedState=PanelInteraction(source:mergedSnapshot)
_ = mergedState.submit(["set-module","chatgpt","true"])
let selectedEntry=mergedState.displayed()!.moduleOptions![0]
expect(selectedEntry.checked==true,"Merged row responds to a toggle immediately")
expect(selectedEntry.children.first?.action==["choose-client-home","chatgpt"],"Optimistic selection preserves connection actions")
expect(selectedEntry.value=="已配置","Selecting does not change configuration state")
expect(selectedEntry.key==mergedEntry.key,"Open configuration retains its anchor after the checkbox changes")
expect(MenuLayout.rowHeight(mergedEntry,width:264,left:36)==27,"Status and checkmark fit on a single compact row")
expect(usageColor("normal")==NSColor.labelColor,"Normal usage adapts to light and dark appearance")
expect(usageColor("warning")==NSColor.systemOrange,"High usage uses system orange")
expect(usageColor("critical")==NSColor.systemRed,"Critical usage uses system red")
expect(usageColor(nil)==nil,"Stale or missing usage keeps its neutral state")
// Hover follows the pointer: the delay counts from entering a row, not from the last movement.
// Which closure fires tells whether a delay restarted, so each step waits for its firing instead of a
// fixed time that a busy CI machine can overrun.
let intent=HoverIntent();intent.delay=0.2;var fired:[String]=[]
func awaitFired(_ count:Int){let deadline=Date().addingTimeInterval(2);while fired.count<count && Date()<deadline{RunLoop.main.run(until:Date().addingTimeInterval(0.02))}}
intent.schedule("0:a"){fired.append("a")}
intent.schedule("0:a"){fired.append("a-restarted")}
awaitFired(1)
expect(fired==["a"],"Moving inside a row must not restart its hover delay")
intent.schedule("0:b"){fired.append("b")};intent.schedule("0:c"){fired.append("c")}
awaitFired(2)
expect(fired==["a","c"],"Only the row the pointer reached last may open")
intent.schedule("0:a"){fired.append("a-again")}
awaitFired(3)
expect(fired.last=="a-again","Returning to a row after its flyout opened schedules it again")
intent.schedule("0:b"){fired.append("cancelled")};intent.cancel()
RunLoop.main.run(until:Date().addingTimeInterval(0.3))
expect(fired.last=="a-again" && intent.target==nil,"Cancelled hover never fires")
expect(MenuLayout.highlighted(pointerInside:false,expanded:true,yielded:true)==false,"An open row yields its highlight to the hovered sibling")
expect(MenuLayout.highlighted(pointerInside:false,expanded:true,yielded:false),"An open row stays highlighted while its flyout is in use")
expect(MenuLayout.highlighted(pointerInside:true,expanded:false,yielded:false),"The row under the pointer is highlighted at once")
let openFlyout=NSRect(x:400,y:300,width:240,height:200)
expect(MenuLayout.aiming(from:NSPoint(x:330,y:480),to:NSPoint(x:350,y:470),flyout:openFlyout),"Diagonal movement into the flyout keeps it open")
expect(!MenuLayout.aiming(from:NSPoint(x:350,y:480),to:NSPoint(x:350,y:450),flyout:openFlyout),"Moving straight down the list switches rows")
expect(!MenuLayout.aiming(from:NSPoint(x:350,y:480),to:NSPoint(x:330,y:470),flyout:openFlyout),"Moving away from the flyout switches rows")
expect(!MenuLayout.aiming(from:NSPoint(x:330,y:480),to:NSPoint(x:340,y:250),flyout:openFlyout),"Steep movement below the flyout switches rows")
expect(MenuLayout.aiming(from:NSPoint(x:700,y:480),to:NSPoint(x:680,y:470),flyout:NSRect(x:400,y:300,width:240,height:200)),"Left-opening flyouts use their right edge")
// Key tests report in place; dialogs still close the panel first.
expect(PanelInteraction.keepsPanelOpen(["test-api-key","claude-api"]),"Testing a key keeps the panel open")
expect(!PanelInteraction.keepsPanelOpen(["enter-api-key","claude-api"]),"Typing a key closes the panel for its dialog")
expect(PanelInteraction.keepsPanelOpen(["set-module","claude-api","true"]),"Preferences keep the panel open")
func keyRow(_ key:String)->Entry {try! JSONDecoder().decode(Entry.self,from:Data("{\"label\":\"测试 Key\",\"action\":[\"test-api-key\",\"\(key)\"],\"children\":[]}".utf8))}
expect(PanelInteraction.runningLabel(keyRow("claude-api"),active:["test-api-key","claude-api"])=="正在测试…","The tested key shows progress")
expect(PanelInteraction.runningLabel(keyRow("chatgpt-api"),active:["test-api-key","claude-api"])==nil,"Other keys are not marked as testing")
expect(PanelInteraction.runningLabel(keyRow("claude-api"),active:nil)==nil,"Idle rows keep their label")
// Provider cards: one stats line, normal states stay quiet, notes that change the reading stay visible.
var card=ProviderUsage(id:"claude",name:"Claude",subtitle:"",status:"已同步",available:true,cost:1,tokens:1000,requests:731,output:810000,cache:0.992,coverage:1,sessions:2,unpriced:0,summaryIncluded:true,models:[],history:[])
expect(DashboardView.stats(card)=="731 请求 · 缓存 99.2% · 输出 810.0K · 2 会话","Request, cache, output and session counts share one line")
expect(DashboardView.note(card)==nil,"A synced provider shows no status text")
card.subtitle="未计入 Summary";expect(DashboardView.note(card)?.0=="未计入 Summary","A note that changes how to read the totals is shown")
card.status="部分记录未读取";expect(DashboardView.note(card)?.1==true,"Problems take the slot and are highlighted")
// Qoder plan requests log Credits only: the card leads with Credits and never shows them as dollars or tokens.
var qoder=ProviderUsage(id:"qoder",name:"Qoder",subtitle:"",status:"已同步",available:true,cost:nil,credits:2.73,tokens:0,requests:3,output:0,cache:nil,coverage:0,sessions:1,unpriced:3,summaryIncluded:true,models:[ModelUsage(name:"qfmodel",tokens:0,share:1,credits:2.73)],history:[])
expect(headline(qoder)=="2.7 Credits" && tokenLine(qoder)=="Token 未记录","Credit-only cards lead with Credits")
expect(DashboardView.stats(qoder)=="3 请求 · 1 会话" && modelAmount(qoder.models[0])=="2.7 Credits","Credit-only stats skip cache and output")
qoder.cost=0.5;qoder.tokens=1200;qoder.output=300;qoder.cache=0.25
expect(headline(qoder)=="$0.50" && DashboardView.stats(qoder).hasSuffix("2.7 Credits"),"Priced tokens lead; Credits move to the stats line")
expect(creditsText(1234.4)=="1234 Credits","Large Credits drop decimals")
// Turning folding back on folds every card at once; replaying saved settings does not.
var folding=PanelInteraction(source:initial)
_ = folding.submit(["set-expanded","claude","true"]);folding.finish(saved(40,["claude"]))
_ = folding.submit(["set-collapse","false"]);folding.finish(PresentationPreferences(revision:41,visible:["summary","opencodex","claude"],appearance:"glass",allowCollapse:false,expandedProviders:["claude"]))
expect(expanded(folding)==Set(["claude"]),"Saved expansions are kept while folding is off")
_ = folding.submit(["set-collapse","true"])
expect(folding.displayed()?.dashboard?.allowCollapse==true && expanded(folding).isEmpty,"Enabling folding collapses every card immediately")
// The legend justifies its columns across the full width, three or four per row.
let legendLabels=["总量","OpenCodex","Claude","OpenCode","DeepSeek","WorkBuddy","Gemini CLI"]
let legendRows=DashboardView.legendLayout(legendLabels,width:320)
expect(legendRows.map{$0.count}==[4,3],"Seven short entries fill two balanced rows of four and three")
let starts=legendRows[0].map{$0.minX}
expect(starts[0]==0 && legendRows[0].last!.maxX==320 && abs((starts[1]-starts[0])-(starts[3]-starts[2]))<0.5,"Columns start at the left edge, end at the right edge and are evenly spaced")
for (i,cell) in legendRows[1].enumerated(){expect(cell.minX==legendRows[0][i].minX,"Each legend column starts at the same x on every row")}
expect(DashboardView.legendLayout(Array(legendLabels.prefix(6)),width:320).map{$0.count}==[3,3],"Six entries split three and three")
let wide=DashboardView.legendLayout(["总量","OpenCodex","Claude","OpenCode","Very Long Harness Name","WorkBuddy","Gemini CLI"],width:320)
expect(wide.map{$0.count}==[3,3,1] && wide[0][2].maxX==320 && wide[2][0].minX==0,"A label too long for four columns falls back to three")
let crowded=DashboardView.legendLayout((1...8).map{"Very long harness name \($0)"},width:320)
expect(crowded.allSatisfy{$0.count<=3} && crowded.flatMap{$0}.allSatisfy{$0.minX>=0 && $0.maxX<=320},"Wide labels stay inside the width")
var legendData=grouped;legendData.providers=[("deepseek","DeepSeek Harness"),("codex","ChatGPT"),("claude","Claude App")].map{var p=grouped.providers[0];p.id=$0.0;p.name=$0.1;p.summaryIncluded=true;return p}
expect(DashboardView.legendLabels(legendData)==["总量","DeepSeek","ChatGPT","Claude"],"Legend names drop suffixes the cards already show")
var gateway=grouped.providers[0];gateway.available=true;gateway.clients=nil
let plainHeight=DashboardView.sectionHeight(gateway)
gateway.clients=[ModelUsage(name:"ChatGPT",tokens:60,share:0.6,credits:nil),ModelUsage(name:"Claude Code",tokens:40,share:0.4,credits:nil)]
expect(DashboardView.clientLine(gateway)=="客户端 · ChatGPT 60% · Claude Code 40%" && DashboardView.sectionHeight(gateway)==plainHeight+16,"A gateway card adds one line naming its clients")
gateway.available=false
expect(DashboardView.clientLine(gateway)==nil,"A card without data names no clients")
// The usage calendar: Monday-first weeks from 2026-05-11 up to Wednesday 2026-11-04.
var days=UsageCalendar(start:"2026-05-11",tokens:[Double](repeating:0,count:178),requests:[Int](repeating:0,count:178),costs:[Double](repeating:0,count:178),unpriced:[Int](repeating:0,count:178),status:"")
for (i,v) in [(170,10.0),(171,20),(172,30),(173,40),(176,5),(177,40)] {days.tokens[i]=v;days.requests[i]=1}
days.requests[100]=3
expect(days.levels[177]==4 && days.levels[173]==4 && days.levels[172]==3 && days.levels[170]==2 && days.levels[176]==1,"Shades go by rank, and the busiest day is the darkest")
expect(days.levels[100]==1 && days.levels[0]==0,"Requests without tokens take the lightest shade; idle days none")
expect(days.summary=="活跃 7 天 · 连续 2 天 · 最长 4 天","The figures line counts active days and runs")
days.requests[177]=0;days.tokens[177]=0
expect(days.streaks.current==1,"Before today's first request the run counts up to yesterday")
days.tokens[177]=40;days.requests[177]=2;days.costs[177]=0.25;days.unpriced[177]=1
expect(days.detail(177)=="11月4日 周三 · 40 Token · 已知 $0.25 · 2 请求" && days.detail(1)=="5月12日 周二 · 无用量","Pointing at a day reads it out")
expect(days.months.map{"\($0.week):\($0.label)"}==["0:5月","3:6月","7:7月","11:8月","16:9月","20:10月","24:11月"],"Months are named over the week holding their first day")
days.status="待同步"
expect(days.summary=="待同步","Before the first read the calendar says so")
var withoutCalendar=grouped;withoutCalendar.showSummary=true
var calendarData=withoutCalendar;calendarData.calendar=days
let calendarGrid=DashboardView.calendarGrid(calendarData,width:344)
expect(abs(calendarGrid.maxX-332)<0.01 && DashboardView.height(calendarData)==DashboardView.height(withoutCalendar)+DashboardView.calendarHeight(calendarData),"The calendar spans the figures above and adds its own height")
expect(DashboardView.calendarDay(at:NSPoint(x:calendarGrid.minX+1,y:calendarGrid.minY+1),calendarData,width:344)==0,"The top-left cell is the first Monday")
expect(DashboardView.calendarDay(at:NSPoint(x:calendarGrid.maxX-1,y:calendarGrid.minY+2.5*DashboardView.calendarPitch(width:344)),calendarData,width:344)==177,"The last column holds this week")
expect(DashboardView.calendarDay(at:NSPoint(x:calendarGrid.maxX-1,y:calendarGrid.maxY-1),calendarData,width:344)==nil && DashboardView.calendarDay(at:NSPoint(x:calendarGrid.minX-6,y:calendarGrid.minY),calendarData,width:344)==nil,"Days after today and points outside the grid read nothing")
calendarData.allowCollapse=true
let calendarFold=DashboardView(calendarData,width:344).subviews.compactMap{$0 as? DisclosureButton}.first!
expect(calendarFold.frame.minY==DashboardView.summaryHeight(calendarData)+DashboardView.calendarHeight(calendarData)+DashboardView.harnessHeaderHeight(calendarData)+2,"Folded cards start below the calendar")
expect(!MenuLayout.aiming(from:NSPoint(x:330,y:480),to:NSPoint(x:332,y:479),flyout:openFlyout),"A tiny drift toward the flyout is not aiming")
// The share picture is opaque, signed, and lists every source that has data.
var shareSource=grouped;shareSource.providers[0].available=false
for style in ShareCard.Style.allCases {
    let card=ShareCard.make(style,shareSource,project:Project(name:"Token Monitor",url:"https://github.com/timecatbit/swiftbar-token-monitor"))
    expect(card.dashboard.providers.allSatisfy{$0.available} && card.dashboard.providers.count==shareSource.providers.filter{$0.available}.count,"Share \(style) keeps every source with data, as the panel does")
    let picture=card.png().flatMap{NSBitmapImageRep(data:$0)}
    expect(card.bounds.height>200 && picture?.pixelsWide==Int(card.bounds.width*2) && picture?.hasAlpha==true && picture.map{$0.colorAt(x:2,y:2)?.alphaComponent==1} == true,"Share \(style) picture is a 2x opaque PNG")
}
expect(ShareCard.Style.allCases.map{$0.title}==["卡片风格","终端风格"],"People pick the card or the terminal style when they share")
let shareCard=ShareCard.make(.card,shareSource,project:Project(name:"Token Monitor",url:"https://github.com/timecatbit/swiftbar-token-monitor"))
let clip=shareCard.png().map{ShareCard.pasteboardItem($0)}
expect(clip.map{Set($0.types)==Set([NSPasteboard.PasteboardType.png,.tiff])}==true,"Copied picture carries PNG and TIFF, not a file URL")
expect(PanelInteraction.keepsPanelOpen(["retry-claude-usage"]),"Retrying Claude usage keeps the panel open")
print("Native interaction assertions: \(assertions) passed")
