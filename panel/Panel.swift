import AppKit
import CoreGraphics
import Darwin

struct Entry: Decodable {
    var label:String;var separator:Bool?;var image:String?;var template:Bool?;var symbol:String?
    var section:Bool?;var attention:Bool?;var checked:Bool?;var detail:String?;var value:String?;var fallbacks:[String]?;var usageLevel:String?;var statusLight:String?;var radio:Bool?;var href:String?;var action:[String]?;var onOpenAction:[String]?;var children:[Entry]
    var actionable:Bool { !children.isEmpty || href != nil || action != nil }
    var isModule:Bool {action?.first=="set-module"}
    var key:String {if isModule,let action{return action.prefix(2).joined(separator:"\u{001F}")}
        return action?.joined(separator:"\u{001F}") ?? href ?? label.components(separatedBy:" · ").first ?? label}
}
struct ModelUsage:Decodable {var name:String;var tokens:Double;var share:Double;var credits:Double?}
struct CountedUsage:Decodable {var cost:Double?;var tokens:Double;var requests:Int;var unpriced:Int;var cache:Double?;var history:[Double]}
struct ProviderUsage:Decodable {
    var id:String;var name:String;var subtitle:String;var status:String;var available:Bool
    var cost:Double?;var credits:Double?;var tokens:Double;var requests:Int;var output:Double;var cache:Double?;var coverage:Double?
    var sessions:Int;var unpriced:Int;var summaryIncluded:Bool?;var counted:CountedUsage?;var models:[ModelUsage];var clients:[ModelUsage]?;var history:[Double]
    // What Summary adds for this source: nothing when it isn't counted, and only the requests no gateway
    // logged when a gateway (OpenCodex, Magpie) counts the rest. The card itself keeps every record.
    var summaryPart:ProviderUsage? {
        guard summaryIncluded != false else{return nil}
        guard let c=counted else{return self}
        var part=self;part.cost=c.cost;part.tokens=c.tokens;part.requests=c.requests;part.unpriced=c.unpriced;part.cache=c.cache;part.history=c.history
        return part
    }
}
// Each day from a Monday 20 weeks back up to today, as Summary counts it.
struct UsageCalendar:Decodable {
    var start:String;var tokens:[Double];var requests:[Int];var costs:[Double];var unpriced:[Int];var status:String?
    // The weeks read, and the fewest shown, so someone who just started still sees a month.
    static let maxWeeks=21,minWeeks=4
    var active:[Bool] {tokens.indices.map{tokens[$0]>0 || (requests.indices.contains($0) && requests[$0]>0)}}
    // Four shades by rank among the days with tokens, so a few heavy days don't wash out the rest and the
    // busiest day is always the darkest. Days with requests but no tokens (Qoder CN Credits) take the lightest.
    var levels:[Int] {
        let values=tokens.filter{$0>0}.sorted(),flags=active
        return tokens.indices.map{i in
            guard flags[i] else{return 0}
            guard tokens[i]>0 else{return 1}
            var low=0,high=values.count
            while low<high {let mid=(low+high)/2;if values[mid]<=tokens[i]{low=mid+1}else{high=mid}}
            return max(1,min(4,Int(ceil(4*Double(low)/Double(values.count)))))
        }
    }
    // Active days, the run that ends today (or yesterday, before today's first request) and the longest run.
    var streaks:(active:Int,current:Int,longest:Int) {
        let flags=active;var longest=0,run=0
        for flag in flags {run=flag ? run+1 : 0;longest=max(longest,run)}
        var current=0,index=flags.count-1
        if index>=0 && !flags[index]{index-=1}
        while index>=0 && flags[index]{current+=1;index-=1}
        return (flags.filter{$0}.count,current,longest)
    }
    // Weeks from the first one with any use up to this one: at least a month, never an empty stretch before.
    var shownWeeks:Int {
        let weeks=(tokens.count+6)/7
        guard let first=active.firstIndex(of:true) else{return max(1,min(weeks,Self.minWeeks))}
        return max(1,min(weeks,max(Self.minWeeks,weeks-first/7)))
    }
    var firstShown:Int {max(0,(tokens.count+6)/7-shownWeeks)*7}
    static let calendar:Calendar={var c=Calendar(identifier:.gregorian);c.timeZone=TimeZone(identifier:"UTC")!;return c}()
    var firstDay:Date? {let f=DateFormatter();f.calendar=Self.calendar;f.timeZone=Self.calendar.timeZone;f.dateFormat="yyyy-MM-dd";return f.date(from:start)}
    func day(_ index:Int)->Date? {firstDay.flatMap{Self.calendar.date(byAdding:.day,value:index,to:$0)}}
    // A month is named over the shown week holding its first day; the first week's month only when there's room.
    var months:[(week:Int,label:String)] {
        guard firstDay != nil else{return []}
        var result:[(week:Int,label:String)]=[]
        for week in 0..<shownWeeks {
            for row in 0..<7 {
                guard let date=day(firstShown+week*7+row),Self.calendar.component(.day,from:date)==1 else{continue}
                result.append((week,"\(Self.calendar.component(.month,from:date))月"))
            }
        }
        if let date=day(firstShown),result.first.map({$0.week>=3}) ?? true {result.insert((0,"\(Self.calendar.component(.month,from:date))月"),at:0)}
        return result
    }
    // The day pointed at: its date, and its tokens, known cost and requests in the figures' rows.
    func detail(_ index:Int)->(date:String,rows:[(label:String,value:String)]) {
        guard let date=day(index) else{return ("",[])}
        let f=DateFormatter();f.calendar=Self.calendar;f.timeZone=Self.calendar.timeZone;f.locale=Locale(identifier:"zh_CN");f.dateFormat="M月d日 EEE"
        let used=tokens.indices.contains(index) ? tokens[index] : 0,n=requests.indices.contains(index) ? requests[index] : 0
        let cost=costs.indices.contains(index) ? costs[index] : 0,partial=unpriced.indices.contains(index) && unpriced[index]>0
        return (f.string(from:date),[("Token",tokenText(used)),("费用",cost>0 ? (partial ? "已知 " : "")+money(cost) : "—"),("请求","\(n)")])
    }
    var figures:[(label:String,value:String)] {let s=streaks;return [("活跃","\(s.active) 天"),("连续","\(s.current) 天"),("最长","\(s.longest) 天")]}
    // Said in place of the figures before the first read, or when nothing has been used yet.
    var note:String? {
        if let status,!status.isEmpty {return status}
        return active.contains(true) ? nil : "暂无用量"
    }
}
struct Dashboard:Decodable {var date:String;var dates:[String];var providers:[ProviderUsage];var rangeKey:String;var rangeLabel:String;var chartUnit:String;var chartSpan:String;var period:[String]?;var chartStatus:String;var updated:String?;var chartPositions:[Double]?;var calendar:UsageCalendar?;var showSummary:Bool?;var allowCollapse:Bool?;var expandedProviders:[String]?}
struct Project:Decodable {var name:String;var url:String}
struct Snapshot:Decodable {var project:Project?;var dashboard:Dashboard?;var entries:[Entry];var rangeOptions:[Entry]?;var updateCount:Int?;var moduleOptions:[Entry]?;var appearance:String?;var preferences:PresentationPreferences?}
func dark(_ view:NSView)->Bool {view.effectiveAppearance.bestMatch(from:[.darkAqua,.aqua]) == .darkAqua}
func background(_ view:NSView)->NSColor {dark(view) ? NSColor(calibratedWhite:0.115,alpha:1) : NSColor(calibratedWhite:0.992,alpha:1)}
func accent(_ id:String,_ view:NSView)->NSColor {
    if id=="claude" {return dark(view) ? NSColor(srgbRed:0.91,green:0.65,blue:0.46,alpha:1) : NSColor(srgbRed:0.75,green:0.40,blue:0.25,alpha:1)}
    if id=="codex" {return .systemPurple}
    if id=="opencode" {return .systemPink}
    if id=="workbuddy" {return .systemOrange}
    if id=="qoder" {return .systemGreen}
    if id=="gemini" {return .systemTeal}
    if id=="deepseek-harness" {return .systemIndigo}
    if id=="magpie" {return .systemYellow}
    return dark(view) ? NSColor(srgbRed:0.55,green:0.71,blue:0.94,alpha:1) : NSColor(srgbRed:0.32,green:0.51,blue:0.78,alpha:1)
}
func statusGreen(_ view:NSView)->NSColor {dark(view) ? NSColor(srgbRed:0.49,green:0.79,blue:0.61,alpha:1) : NSColor(srgbRed:0.18,green:0.49,blue:0.31,alpha:1)}
// One status light for every row: new (accent), ok (green), issue (orange), anything else a hollow grey ring.
func drawStatusLight(_ state:String,centre:NSPoint,in view:NSView){
    let dot=NSBezierPath(ovalIn:NSRect(x:centre.x-3.5,y:centre.y-3.5,width:7,height:7))
    switch state {
    case "new":NSColor.controlAccentColor.setFill();dot.fill()
    case "ok":statusGreen(view).setFill();dot.fill()
    case "issue":NSColor.systemOrange.setFill();dot.fill()
    default:NSColor.secondaryLabelColor.setStroke();dot.lineWidth=1;dot.stroke()
    }
}
func usageColor(_ level:String?)->NSColor? {
    switch level {case "normal":return .labelColor;case "warning":return .systemOrange;case "critical":return .systemRed;default:return nil}
}
func share(_ ratio:Double)->String {let value=ratio*100;return String(format:(value>0 && value<1)||(value>99 && value<100) ? "%.1f%%" : "%.0f%%",value)}
func tokens(_ value:Double)->String {
    if value>=1e9{return String(format:"%.2fB",value/1e9)}
    if value>=1e6{return String(format:"%.2fM",value/1e6)}
    if value>=1e3{return String(format:"%.1fK",value/1e3)}
    return String(format:"%.0f",value)
}
// The same figure where a type's own `tokens` property hides the function.
func tokenText(_ value:Double)->String {tokens(value)}
func axisTokens(_ value:Double)->String {
    for (unit,suffix) in [(1e9,"B"),(1e6,"M"),(1e3,"K")] where value>=unit {
        let scaled=value/unit
        return String(format:scaled>=10 || scaled==floor(scaled) ? "%.0f%@" : "%.1f%@",scaled,suffix)
    }
    return String(format:"%.0f",value)
}
func modelName(_ name:String)->String {
    guard name.hasPrefix("claude-") else{return name}
    let value=name.replacingOccurrences(of:"-20[0-9]{6}$",with:"",options:.regularExpression)
    let parts=value.dropFirst(7).split(separator:"-").map(String.init)
    guard let family=parts.first,["opus","sonnet","haiku","fable"].contains(family) else{return name}
    return family.capitalized+" "+parts.dropFirst().joined(separator:".")
}
func money(_ value:Double?)->String {value.map{String(format:"$%.2f",$0)} ?? "—"}
// Qoder CN plan requests log Qoder Credits and no tokens; Credits are never shown as dollars or tokens.
func creditsText(_ value:Double)->String {String(format:value>=100 ? "%.0f Credits" : "%.1f Credits",value)}
func headline(_ p:ProviderUsage)->String {
    if p.cost==nil,let credits=p.credits {return creditsText(credits)}
    return money(p.cost)
}
func tokenLine(_ p:ProviderUsage)->String {p.tokens==0 && p.credits != nil ? "Token 未记录" : tokens(p.tokens)+" Token"}
func modelAmount(_ m:ModelUsage)->String {
    if m.tokens==0,let credits=m.credits {return creditsText(credits)}
    return tokens(m.tokens)
}
func text(_ value:String,x:CGFloat,y:CGFloat,w:CGFloat,size:CGFloat=11,color:NSColor = .labelColor,weight:NSFont.Weight = .regular,align:NSTextAlignment = .left,numeric:Bool=false) {
    let style=NSMutableParagraphStyle();style.alignment=align;style.lineBreakMode = .byTruncatingTail
    var font=numeric ? NSFont.monospacedDigitSystemFont(ofSize:size,weight:weight) : NSFont.systemFont(ofSize:size,weight:weight)
    if numeric {let measured=(value as NSString).size(withAttributes:[.font:font]).width;if measured>w {font=NSFont.monospacedDigitSystemFont(ofSize:max(9,size*w/measured),weight:weight)}}
    (value as NSString).draw(in:NSRect(x:x,y:y,width:w,height:size+6),withAttributes:[.font:font,.foregroundColor:color,.paragraphStyle:style])
}
func line(_ a:NSPoint,_ b:NSPoint,color:NSColor = .separatorColor,width:CGFloat=0.5) {
    let p=NSBezierPath();p.move(to:a);p.line(to:b);p.lineWidth=width;color.setStroke();p.stroke()
}
class FlippedView:NSView {override var isFlipped:Bool {true}}
final class Surface:FlippedView {
    let content:FlippedView
    private let translucent:Bool
    init(frame:NSRect,glass:Bool=true){
        content=FlippedView(frame:NSRect(origin:.zero,size:frame.size))
        translucent=glass && !NSWorkspace.shared.accessibilityDisplayShouldReduceTransparency
        super.init(frame:frame);wantsLayer=true;layer?.cornerRadius=13;layer?.masksToBounds=true
        content.autoresizingMask=[.width,.height]
        if translucent {
            #if compiler(>=6.2)
            if #available(macOS 26.0,*) {
                let effect=NSGlassEffectView(frame:bounds);effect.style = .regular;effect.cornerRadius=13
                effect.autoresizingMask=[.width,.height];effect.contentView=content;addSubview(effect)
                return
            }
            #endif
            let effect=NSVisualEffectView(frame:bounds);effect.material = .popover;effect.blendingMode = .behindWindow
            effect.state = .active;effect.autoresizingMask=[.width,.height];effect.addSubview(content);addSubview(effect)
        } else {addSubview(content)}
    }
    required init?(coder:NSCoder){fatalError()}
    override func viewDidChangeEffectiveAppearance(){super.viewDidChangeEffectiveAppearance();redraw(self)}
    private func redraw(_ view:NSView){view.needsDisplay=true;for child in view.subviews{redraw(child)}}
    override func draw(_ dirtyRect:NSRect){
        if !translucent {background(self).setFill();bounds.fill()}
        let shape=NSBezierPath(roundedRect:bounds.insetBy(dx:0.25,dy:0.25),xRadius:13,yRadius:13)
        NSColor.separatorColor.withAlphaComponent(0.35).setStroke();shape.lineWidth=0.5;shape.stroke()
    }
}
// Section divider on the homepage: a little heavier than a hairline so groups read apart.
final class Divider:NSView {
    override func draw(_ dirtyRect:NSRect){NSColor.labelColor.withAlphaComponent(0.16).setFill();NSBezierPath(roundedRect:bounds,xRadius:1,yRadius:1).fill()}
}
final class Panel:NSPanel {
    var escape:(()->Void)?
    override var canBecomeKey:Bool {true}
    override func keyDown(with event:NSEvent){if event.keyCode==53{escape?()}else{super.keyDown(with:event)}}
}
final class ContentScrollView:NSScrollView {
    var needsVerticalScrolling:Bool{(documentView?.frame.height ?? 0)>contentView.bounds.height+0.5}
    func configureScrolling(){
        hasHorizontalScroller=false;horizontalScrollElasticity = .none
        hasVerticalScroller=needsVerticalScrolling
        verticalScrollElasticity=needsVerticalScrolling ? .automatic : .none
        if !needsVerticalScrolling{contentView.scroll(to:.zero);reflectScrolledClipView(contentView)}
    }
    override func scrollWheel(with event:NSEvent){
        guard needsVerticalScrolling else{return}
        super.scrollWheel(with:event)
    }
}
// The panel never activates the app, and AppKit only shows tooltips for the active app, so header
// buttons report hovering themselves (activeAlways) and the panel draws a short hint below them.
final class HintButton:NSButton {
    var hint="";var hovered:((HintButton,Bool)->Void)?;private var area:NSTrackingArea?
    override func updateTrackingAreas(){super.updateTrackingAreas();if let area{removeTrackingArea(area)};area=NSTrackingArea(rect:bounds,options:[.mouseEnteredAndExited,.activeAlways,.inVisibleRect],owner:self,userInfo:nil);addTrackingArea(area!)}
    override func mouseEntered(with event:NSEvent){hovered?(self,true)}
    override func mouseExited(with event:NSEvent){hovered?(self,false)}
    // A click hides the hint first, so it never sits under the menu the click opens.
    override func mouseDown(with event:NSEvent){hovered?(self,false);super.mouseDown(with:event)}
}
final class DisclosureButton:NSButton {
    override func acceptsFirstMouse(for event:NSEvent?)->Bool{true}
    var invoke:(()->Void)?;var hovered:((Bool)->Void)?;private var area:NSTrackingArea?
    init(label:String,frame:NSRect){super.init(frame:frame);title=label;isBordered=false;setButtonType(.momentaryChange);target=self;action=#selector(pressed);setAccessibilityLabel(label)}
    required init?(coder:NSCoder){fatalError()}
    @objc private func pressed(){invoke?()}
    override func draw(_ dirtyRect:NSRect){}
    override func updateTrackingAreas(){super.updateTrackingAreas();if let area{removeTrackingArea(area)};area=NSTrackingArea(rect:bounds,options:[.mouseEnteredAndExited,.activeAlways,.inVisibleRect],owner:self,userInfo:nil);addTrackingArea(area!)}
    override func mouseEntered(with event:NSEvent){hovered?(true)}
    override func mouseExited(with event:NSEvent){hovered?(false)}
}
// The share picture has its own layout, never the panel's glass, because what shows through the
// glass depends on each user's desktop. People pick its style when they copy or share it: a card, or
// a terminal window. It covers the usage that Summary counts; subscriptions, balances and keys never appear.
class ShareCard:FlippedView {
    enum Style:Int,CaseIterable {
        case card,terminal
        var title:String {self == .card ? "卡片风格" : "终端风格"}
    }
    static let width:CGFloat=420
    let dashboard:Dashboard,project:Project
    var rows:[ProviderUsage] {dashboard.providers}
    // Totals, chart and the share bar use what Summary counts; every source with data is listed.
    var counted:[ProviderUsage] {rows.compactMap{$0.summaryPart}}
    var shared:[ProviderUsage] {counted.filter{$0.tokens>0}}
    var pictureHeight:CGFloat {0}
    static func make(_ style:Style,_ dashboard:Dashboard,project:Project)->ShareCard {
        style == .terminal ? TerminalShareCard(dashboard,project:project) : CardShareCard(dashboard,project:project)
    }
    init(_ dashboard:Dashboard,project:Project){
        // Every source with data, in the panel's order; those Summary doesn't count are tagged, not moved.
        var shown=dashboard
        shown.providers=dashboard.providers.filter{$0.available}
        self.dashboard=shown;self.project=project
        super.init(frame:.zero)
        frame=NSRect(x:0,y:0,width:Self.width,height:pictureHeight)
    }
    required init?(coder:NSCoder){fatalError()}
    // A gateway's clients, wrapped onto as many lines as the picture needs rather than cut off.
    static func clientLines(_ p:ProviderUsage,width:CGFloat,font:NSFont)->[String] {
        guard DashboardView.clientLine(p) != nil,let clients=p.clients else{return []}
        var lines:[String]=[],current="客户端"
        for item in clients.map({$0.name+" "+share($0.share)}) {
            let longer=current+" · "+item
            if (longer as NSString).size(withAttributes:[.font:font]).width>width && current != "客户端" {lines.append(current);current=item}
            else {current=longer}
        }
        return lines+[current]
    }
    static func title(_ d:Dashboard)->String {
        ["today":"今日","24h":"近 24 小时","7d":"近 7 天","30d":"近 30 天","week":"本周","month":"本月","all":"累计"][d.rangeKey].map{$0+" AI 用量"} ?? d.rangeLabel+" AI 用量"
    }
    func measure(_ value:String,_ font:NSFont)->CGFloat {ceil((value as NSString).size(withAttributes:[.font:font]).width)}
    func draw(_ value:String,_ rect:NSRect,_ font:NSFont,_ color:NSColor,_ align:NSTextAlignment = .left){
        let style=NSMutableParagraphStyle();style.alignment=align;style.lineBreakMode = .byTruncatingTail
        (value as NSString).draw(in:rect,withAttributes:[.font:font,.foregroundColor:color,.paragraphStyle:style])
    }
    // Text centred on a line by its glyph box, so different fonts side by side share one middle.
    func draw(_ value:String,x:CGFloat,middle:CGFloat,width:CGFloat,_ font:NSFont,_ color:NSColor,_ align:NSTextAlignment = .left){
        let height=ceil(font.ascender-font.descender)
        draw(value,NSRect(x:x,y:middle-height/2,width:width,height:height+2),font,color,align)
    }
    func symbol(_ name:String,centre:NSPoint,size:CGFloat,color:NSColor,weight:NSFont.Weight = .semibold){
        let config=NSImage.SymbolConfiguration(pointSize:size,weight:weight).applying(NSImage.SymbolConfiguration(paletteColors:[color]))
        guard let image=NSImage(systemSymbolName:name,accessibilityDescription:nil)?.withSymbolConfiguration(config) else{return}
        let s=image.size
        image.draw(in:NSRect(x:centre.x-s.width/2,y:centre.y-s.height/2,width:s.width,height:s.height),from:.zero,operation:.sourceOver,fraction:1,respectFlipped:true,hints:nil)
    }
    // Dollars with thousands separators: the picture is read at a glance.
    static let dollars:NumberFormatter={let f=NumberFormatter();f.numberStyle = .decimal;f.minimumFractionDigits=2;f.maximumFractionDigits=2;f.locale=Locale(identifier:"en_US");return f}()
    func amount(_ p:ProviderUsage)->String {p.cost.flatMap{Self.dollars.string(from:NSNumber(value:$0))}.map{"$"+$0} ?? headline(p)}
    // The menu bar's star on a small teal tile: the mark that signs the picture.
    func brandMark(_ rect:NSRect){
        let tile=NSBezierPath(roundedRect:rect,xRadius:rect.width*0.3,yRadius:rect.width*0.3)
        NSGradient(starting:NSColor(srgbRed:0.27,green:0.70,blue:0.64,alpha:1),ending:NSColor(srgbRed:0.11,green:0.39,blue:0.45,alpha:1))?.draw(in:tile,angle:-90)
        symbol("star.fill",centre:NSPoint(x:rect.midX,y:rect.midY),size:rect.width*0.5,color:.white,weight:.bold)
    }
    // PNG for apps that take it, TIFF for older ones; no file URL, so chat apps paste a picture.
    static func pasteboardItem(_ png:Data)->NSPasteboardItem {
        let item=NSPasteboardItem();item.setData(png,forType:.png)
        if let tiff=NSBitmapImageRep(data:png)?.tiffRepresentation{item.setData(tiff,forType:.tiff)}
        return item
    }
    // Rendered at 2x so the picture stays sharp on Retina screens and in chat apps.
    func png()->Data? {
        guard let rep=NSBitmapImageRep(bitmapDataPlanes:nil,pixelsWide:Int(bounds.width*2),pixelsHigh:Int(bounds.height*2),bitsPerSample:8,samplesPerPixel:4,hasAlpha:true,isPlanar:false,colorSpaceName:.deviceRGB,bytesPerRow:0,bitsPerPixel:0) else{return nil}
        rep.size=bounds.size
        (appearance ?? NSAppearance.currentDrawing()).performAsCurrentDrawingAppearance{cacheDisplay(in:bounds,to:rep)}
        return rep.representation(using:.png,properties:[:])
    }
}
// The card style: an opaque card on a soft wash lit by the leading tools' colours.
final class CardShareCard:ShareCard {
    static let margin:CGFloat=22,inset:CGFloat=24,chartHeight:CGFloat=150,modelHeight:CGFloat=24,legendRow:CGFloat=20
    static let clientWidth=width-margin*2-inset*2-32,clientFont=NSFont.systemFont(ofSize:10.5)
    static func blockHeight(_ p:ProviderUsage)->CGFloat {92+CGFloat(clientLines(p,width:clientWidth,font:clientFont).count)*18+CGFloat(max(1,p.models.count))*modelHeight}
    private var cardHeight:CGFloat {
        let header:CGFloat=44,hero:CGFloat=88,tiles:CGFloat=88,chart=dashboard.dates.count>1 ? Self.chartHeight+22 : 0
        let share:CGFloat=shared.isEmpty ? 0 : 60+CGFloat((shared.count+2)/3)*Self.legendRow
        let blocks=rows.isEmpty ? 0 : rows.reduce(0){$0+Self.blockHeight($1)+10}-10
        return Self.inset*2+header+hero+tiles+chart+share+blocks
    }
    override var pictureHeight:CGFloat {Self.margin*2+cardHeight+60}
    static func day(_ iso:String)->String {
        let parts=iso.split(separator:"-").compactMap{Int($0)}
        return parts.count==3 ? "\(parts[0]) 年 \(parts[1]) 月 \(parts[2]) 日" : iso
    }
    // What the picture covers: today's date, a 24-hour window with its times, or first to last day.
    static func period(_ d:Dashboard)->String {
        func numbers(_ value:String)->[Int] {value.split(whereSeparator:{"-T:".contains($0)}).compactMap{Int($0)}}
        guard d.rangeKey != "today",let bounds=d.period,bounds.count==2 else{return day(d.date)}
        let a=numbers(bounds[0]),b=numbers(bounds[1])
        guard a.count>=3,b.count==a.count else{return day(d.date)}
        if a.count>=5 {return String(format:"%d/%d %02d:%02d – %d/%d %02d:%02d",a[1],a[2],a[3],a[4],b[1],b[2],b[3],b[4])}
        if a[0] != b[0] {return "\(a[0]) 年 \(a[1]) 月 \(a[2]) 日 – \(b[0]) 年 \(b[1]) 月 \(b[2]) 日"}
        if a[1] != b[1] {return "\(a[0]) 年 \(a[1]) 月 \(a[2]) 日 – \(b[1]) 月 \(b[2]) 日"}
        return a[2] != b[2] ? "\(a[0]) 年 \(a[1]) 月 \(a[2]) – \(b[2]) 日" : "\(a[0]) 年 \(a[1]) 月 \(a[2]) 日"
    }
    private func font(_ size:CGFloat,_ weight:NSFont.Weight,rounded:Bool=false)->NSFont {
        let base=NSFont.systemFont(ofSize:size,weight:weight)
        guard rounded,let descriptor=base.fontDescriptor.withDesign(.rounded) else{return base}
        return NSFont(descriptor:descriptor,size:size) ?? base
    }
    // Large figures shrink to fit their slot rather than truncate.
    private func fitted(_ value:String,_ size:CGFloat,_ weight:NSFont.Weight,room:CGFloat)->NSFont {
        let full=font(size,weight,rounded:true),measured=measure(value,full)
        return measured<=room ? full : font(max(11,floor(size*room/measured)),weight,rounded:true)
    }
    private var teal:NSColor {dark(self) ? NSColor(srgbRed:0.43,green:0.79,blue:0.75,alpha:1) : NSColor(srgbRed:0.15,green:0.47,blue:0.46,alpha:1)}
    override func draw(_ dirtyRect:NSRect){
        let isDark=dark(self)
        // A soft wash lit by the colours of the tools that used the most.
        (isDark ? NSColor(srgbRed:0.05,green:0.06,blue:0.07,alpha:1) : NSColor(srgbRed:0.95,green:0.96,blue:0.97,alpha:1)).setFill();bounds.fill()
        let glows=[teal]+counted.sorted{$0.tokens>$1.tokens}.prefix(2).map{accent($0.id,self)}
        for (c,(fx,fy,fr)) in zip(glows,[(0.08,0.03,0.95),(1.0,0.22,0.8),(0.1,0.78,0.9)] as [(CGFloat,CGFloat,CGFloat)]){
            let centre=NSPoint(x:bounds.width*fx,y:bounds.height*fy)
            NSGradient(starting:c.withAlphaComponent(isDark ? 0.26 : 0.20),ending:c.withAlphaComponent(0))?.draw(fromCenter:centre,radius:0,toCenter:centre,radius:bounds.width*fr,options:[])
        }
        let card=NSRect(x:Self.margin,y:Self.margin,width:bounds.width-Self.margin*2,height:cardHeight),shape=NSBezierPath(roundedRect:card,xRadius:26,yRadius:26)
        NSGraphicsContext.saveGraphicsState()
        let shadow=NSShadow();shadow.shadowColor=NSColor.black.withAlphaComponent(isDark ? 0.45 : 0.09);shadow.shadowBlurRadius=36;shadow.shadowOffset=NSSize(width:0,height:-12);shadow.set()
        (isDark ? NSColor(srgbRed:0.10,green:0.105,blue:0.115,alpha:1) : .white).setFill();shape.fill()
        NSGraphicsContext.restoreGraphicsState()
        let rim=NSBezierPath(roundedRect:card.insetBy(dx:0.5,dy:0.5),xRadius:25.5,yRadius:25.5)
        NSColor.labelColor.withAlphaComponent(isDark ? 0.11 : 0.05).setStroke();rim.lineWidth=1;rim.stroke()
        let left=card.minX+Self.inset,width=card.width-Self.inset*2,right=left+width
        let quiet=NSColor.labelColor.withAlphaComponent(isDark ? 0.055 : 0.035)
        var y=card.minY+Self.inset
        // Header: what and when.
        let day=Self.period(dashboard),dayFont=font(11,.semibold),pillWidth=min(width*0.6,measure(day,dayFont)+32)
        let pill=NSRect(x:right-pillWidth,y:y,width:pillWidth,height:26)
        teal.withAlphaComponent(isDark ? 0.16 : 0.09).setFill();NSBezierPath(roundedRect:pill,xRadius:13,yRadius:13).fill()
        symbol("calendar",centre:NSPoint(x:pill.minX+15,y:pill.midY),size:10,color:teal)
        draw(day,x:pill.minX+24,middle:pill.midY,width:pillWidth-32,dayFont,teal)
        let title=Self.title(dashboard),room=pill.minX-left-10,full=measure(title,font(19,.bold))
        draw(title,x:left,middle:pill.midY,width:room+2,font(full<=room ? 19 : max(15,floor(19*room/full)),.bold),.labelColor)
        y+=44
        // Hero: the estimate, its dollar sign set small against the figure.
        let counted=self.counted,costs=counted.compactMap{$0.cost},partial=counted.contains{$0.cost==nil || $0.unpriced>0}
        draw(partial ? "已知 API 等值估算" : "API 等值估算",NSRect(x:left,y:y,width:width,height:16),font(11.5,.medium),.secondaryLabelColor)
        let hero=NSMutableAttributedString()
        if let total=costs.isEmpty ? nil : costs.reduce(0,+) {
            hero.append(NSAttributedString(string:"$",attributes:[.font:font(26,.semibold,rounded:true),.foregroundColor:NSColor.secondaryLabelColor,.baselineOffset:15]))
            hero.append(NSAttributedString(string:Self.dollars.string(from:NSNumber(value:total)) ?? String(money(total).dropFirst()),attributes:[.font:font(50,.bold,rounded:true),.foregroundColor:NSColor.labelColor,.kern:-0.6]))
        } else {hero.append(NSAttributedString(string:"—",attributes:[.font:font(50,.bold,rounded:true),.foregroundColor:NSColor.labelColor]))}
        hero.draw(in:NSRect(x:left-2,y:y+16,width:width,height:64))
        y+=88
        // Three figures, each with its own small symbol.
        let count=NumberFormatter();count.numberStyle = .decimal
        // Cache hit rate weighted by each tool's tokens; tools that report none are left out.
        let cached=counted.filter{$0.cache != nil},weight=cached.reduce(0){$0+$1.tokens}
        let hit=weight>0 ? String(format:"%.1f%%",cached.reduce(0){$0+($1.cache ?? 0)*$1.tokens}/weight*100) : "—"
        let tiles=[("square.stack.3d.up.fill","Token",tokens(counted.reduce(0){$0+$1.tokens})),
                   ("arrow.up.arrow.down","请求",count.string(from:NSNumber(value:counted.reduce(0){$0+$1.requests})) ?? "0"),
                   ("bolt.fill","缓存命中",hit)]
        let tileWidth=(width-20)/3
        for (i,(icon,label,value)) in tiles.enumerated(){
            let tile=NSRect(x:left+CGFloat(i)*(tileWidth+10),y:y,width:tileWidth,height:66)
            quiet.setFill();NSBezierPath(roundedRect:tile,xRadius:16,yRadius:16).fill()
            symbol(icon,centre:NSPoint(x:tile.minX+19,y:tile.minY+19),size:10,color:teal)
            draw(label,NSRect(x:tile.minX+30,y:tile.minY+11.5,width:tile.width-40,height:15),font(10.5,.medium),.secondaryLabelColor)
            draw(value,NSRect(x:tile.minX+13,y:tile.minY+31,width:tile.width-24,height:26),fitted(value,20,.semibold,room:tile.width-26),.labelColor)
        }
        y+=88
        // Chart: the total as a soft area, each tool as a thin line in its own colour.
        let n=dashboard.dates.count
        if n>1 {
            let series=counted.map{p in (0..<n).map{i in i<p.history.count ? max(0,p.history[i]) : 0}}
            // Same scale as the panel: a rounded ceiling with labelled half and full grid lines.
            let observed=max((0..<n).map{i in series.reduce(0){$0+$1[i]}}.max() ?? 0,1),unit=pow(10,floor(log10(observed)))
            let ceiling=ceil(observed*1.08/unit*2)/2*unit
            let plot=NSRect(x:left,y:y+16,width:width,height:Self.chartHeight-46),axisFont=font(9.5,.medium,rounded:true)
            draw(dashboard.chartUnit+" · Token",NSRect(x:right-160,y:y-2,width:160,height:13),font(9.5,.medium),.tertiaryLabelColor,.right)
            for f in [CGFloat(0),0.5,1]{
                let yy=plot.maxY-f*plot.height,grid=NSBezierPath()
                grid.move(to:NSPoint(x:plot.minX,y:yy));grid.line(to:NSPoint(x:plot.maxX,y:yy))
                if f>0 {grid.setLineDash([2,3],count:2,phase:0);grid.lineWidth=0.75;NSColor.labelColor.withAlphaComponent(isDark ? 0.12 : 0.09).setStroke()}
                else {grid.lineWidth=1;NSColor.labelColor.withAlphaComponent(isDark ? 0.16 : 0.10).setStroke()}
                grid.stroke()
            }
            let positions=DashboardView.chartPositions(dashboard)
            let paths=DashboardView.smoothCurves(series,positions:positions,plot:plot,ceiling:ceiling),total=paths.last!
            let area=total.copy() as! NSBezierPath;area.line(to:NSPoint(x:plot.maxX,y:plot.maxY));area.line(to:NSPoint(x:plot.minX,y:plot.maxY));area.close()
            NSGraphicsContext.saveGraphicsState();area.addClip()
            NSGradient(starting:teal.withAlphaComponent(isDark ? 0.34 : 0.22),ending:teal.withAlphaComponent(0.0))?.draw(from:NSPoint(x:plot.midX,y:plot.minY),to:NSPoint(x:plot.midX,y:plot.maxY),options:[])
            NSGraphicsContext.restoreGraphicsState()
            for (i,curve) in paths.enumerated(){
                let isTotal=i==paths.count-1
                curve.lineWidth=isTotal ? 2.4 : 1.3;curve.lineCapStyle = .round;curve.lineJoinStyle = .round
                (isTotal ? teal : accent(counted[i].id,self).withAlphaComponent(0.85)).setStroke();curve.stroke()
            }
            let surface=isDark ? NSColor(srgbRed:0.10,green:0.105,blue:0.115,alpha:1) : NSColor.white
            for f in [0.5,1.0]{
                let label=axisTokens(ceiling*f),yy=plot.maxY-CGFloat(f)*plot.height
                surface.withAlphaComponent(0.85).setFill();NSBezierPath(roundedRect:NSRect(x:left-3,y:yy-14,width:measure(label,axisFont)+6,height:13),xRadius:3,yRadius:3).fill()
                draw(label,NSRect(x:left,y:yy-14,width:80,height:12),axisFont,.tertiaryLabelColor)
            }
            // The latest point of the total, ringed.
            let end=total.currentPoint
            teal.withAlphaComponent(0.18).setFill();NSBezierPath(ovalIn:NSRect(x:end.x-7,y:end.y-7,width:14,height:14)).fill()
            let dot=NSBezierPath(ovalIn:NSRect(x:end.x-3.5,y:end.y-3.5,width:7,height:7))
            surface.setFill();dot.fill();teal.setStroke();dot.lineWidth=2;dot.stroke()
            let labelFont=font(9.5,.medium,rounded:true),middle=positions.indices.dropFirst().dropLast().min{abs(positions[$0]-0.5)<abs(positions[$1]-0.5)}
            for i in [0]+(middle.map{[$0]} ?? [])+[n-1]{
                let x=plot.minX+CGFloat(positions[i])*plot.width,align:NSTextAlignment=i==0 ? .left : (i==n-1 ? .right : .center)
                draw(dashboard.dates[i],NSRect(x:i==0 ? x : (i==n-1 ? x-80 : x-40),y:plot.maxY+8,width:80,height:13),labelFont,.tertiaryLabelColor,align)
            }
            y+=Self.chartHeight+22
        }
        // Share of tokens: one bar in the tools' colours, then a key that names each colour.
        let sum=max(counted.reduce(0){$0+$1.tokens},1),shared=self.shared
        if !shared.isEmpty {
            draw("Token 占比",NSRect(x:left,y:y,width:width,height:15),font(11,.semibold),.secondaryLabelColor)
            let bar=NSRect(x:left,y:y+24,width:width,height:10)
            NSGraphicsContext.saveGraphicsState();NSBezierPath(roundedRect:bar,xRadius:5,yRadius:5).addClip()
            quiet.setFill();bar.fill()
            // A small share still shows as a sliver; the largest one gives up the room.
            var widths=shared.map{max(10,bar.width*CGFloat($0.tokens/sum))}
            if let widest=widths.indices.max(by:{widths[$0]<widths[$1]}){widths[widest]-=widths.reduce(0,+)-bar.width}
            var x=bar.minX
            for (i,p) in shared.enumerated(){
                accent(p.id,self).setFill();NSRect(x:x,y:bar.minY,width:max(0,widths[i]-(i<shared.count-1 ? 2 : 0)),height:bar.height).fill();x+=widths[i]
            }
            NSGraphicsContext.restoreGraphicsState()
            let column=width/3,keyFont=font(11,.medium),shareFont=font(11,.medium,rounded:true)
            for (i,p) in shared.enumerated(){
                let x=left+CGFloat(i%3)*column,ky=bar.maxY+14+CGFloat(i/3)*Self.legendRow,percent=share(p.tokens/sum)
                accent(p.id,self).setFill();NSBezierPath(ovalIn:NSRect(x:x,y:ky+4,width:7,height:7)).fill()
                let name=DashboardView.legendName(p)
                let nameWidth=min(measure(name,keyFont),column-measure(percent,shareFont)-24)
                draw(name,NSRect(x:x+12,y:ky,width:nameWidth,height:15),keyFont,.labelColor)
                draw(percent,NSRect(x:x+16+nameWidth,y:ky,width:column-nameWidth-16,height:15),shareFont,.secondaryLabelColor)
            }
            y+=60+CGFloat((shared.count+2)/3)*Self.legendRow
        }
        // Each tool on its own tile: what it cost, its share and stats, then every model.
        for p in rows {
            let c=accent(p.id,self),inSummary=p.summaryIncluded != false,block=NSRect(x:left,y:y,width:width,height:Self.blockHeight(p))
            quiet.setFill();NSBezierPath(roundedRect:block,xRadius:18,yRadius:18).fill()
            let il=block.minX+16,ir=block.maxX-16,iw=ir-il,top=block.minY+16
            c.withAlphaComponent(inSummary ? 1 : 0.45).setFill();NSBezierPath(roundedRect:NSRect(x:il,y:top+3,width:4,height:15),xRadius:2,yRadius:2).fill()
            let nameFont=font(14.5,.semibold),amount=amount(p),amountFont=font(17,.semibold,rounded:true),amountWidth=measure(amount,amountFont)
            let nameWidth=min(iw*0.5,measure(p.name,nameFont))
            draw(p.name,NSRect(x:il+12,y:top,width:nameWidth,height:20),nameFont,.labelColor)
            // A source a gateway partly counts says so, as the panel card does: its share is only the rest.
            if !inSummary || (p.counted != nil && !p.subtitle.isEmpty) {
                let tag=inSummary ? p.subtitle : "未计入 Summary",tagFont=font(9.5,.medium)
                let room=ir-amountWidth-10-(il+20+nameWidth),tagWidth=min(room,measure(tag,tagFont)+14)
                let capsule=NSRect(x:il+20+nameWidth,y:top+2,width:tagWidth,height:17)
                NSColor.labelColor.withAlphaComponent(isDark ? 0.09 : 0.06).setFill();NSBezierPath(roundedRect:capsule,xRadius:8.5,yRadius:8.5).fill()
                draw(tag,capsule.insetBy(dx:7,dy:2),tagFont,.secondaryLabelColor,.center)
            }
            draw(amount,NSRect(x:ir-amountWidth-2,y:top-1,width:amountWidth+2,height:22),amountFont,.labelColor,.right)
            let part=p.summaryPart?.tokens ?? 0
            draw(tokenLine(p)+(part>0 ? " · 占 "+share(part/sum) : ""),NSRect(x:il,y:top+24,width:iw,height:16),font(11.5,.medium),.secondaryLabelColor)
            draw(DashboardView.stats(p),NSRect(x:il,y:top+42,width:iw,height:15),font(10.5,.regular),.secondaryLabelColor)
            var my=top+66
            for line in Self.clientLines(p,width:Self.clientWidth,font:Self.clientFont){draw(line,NSRect(x:il,y:my-6,width:iw,height:15),Self.clientFont,.secondaryLabelColor);my+=18}
            if p.models.isEmpty{draw("此时段暂无模型调用",NSRect(x:il,y:my,width:iw,height:15),font(11,.regular),.secondaryLabelColor)}
            for m in p.models{
                draw(modelName(m.name),NSRect(x:il,y:my,width:iw-140,height:16),font(11.5,.regular),.labelColor)
                draw(modelAmount(m),NSRect(x:ir-134,y:my,width:86,height:16),font(11.5,.medium,rounded:true),.labelColor,.right)
                draw(share(m.share),NSRect(x:ir-44,y:my,width:44,height:16),font(11,.medium,rounded:true),.secondaryLabelColor,.right)
                let track=NSRect(x:il,y:my+17,width:iw,height:3)
                NSColor.labelColor.withAlphaComponent(isDark ? 0.08 : 0.06).setFill();NSBezierPath(roundedRect:track,xRadius:1.5,yRadius:1.5).fill()
                let fill=NSRect(x:track.minX,y:track.minY,width:max(3,track.width*CGFloat(min(1,m.share))),height:track.height)
                NSGradient(starting:c.withAlphaComponent(0.55),ending:c)?.draw(in:NSBezierPath(roundedRect:fill,xRadius:1.5,yRadius:1.5),angle:0)
                my+=Self.modelHeight
            }
            y+=block.height+10
        }
        // Signature under the card: the mark, the name and where to find it.
        let footer=card.maxY+20,nameFont=font(13.5,.semibold),nameWidth=measure(project.name,nameFont),rowWidth=20+8+nameWidth
        brandMark(NSRect(x:bounds.midX-rowWidth/2,y:footer,width:20,height:20))
        draw(project.name,NSRect(x:bounds.midX-rowWidth/2+28,y:footer+1,width:nameWidth+2,height:18),nameFont,.labelColor)
        draw(project.url,NSRect(x:0,y:footer+26,width:bounds.width,height:15),font(10.5,.regular),.secondaryLabelColor,.center)
    }
}
// The terminal style: a terminal window on a dot grid.
final class TerminalShareCard:ShareCard {
    static let margin:CGFloat=22,pad:CGFloat=22,titleBar:CGFloat=34,chartHeight:CGFloat=170,modelHeight:CGFloat=20,legendRow:CGFloat=18
    static let clientWidth=width-margin*2-pad*2-16,clientFont=NSFont.monospacedSystemFont(ofSize:10.5,weight:.regular)
    static func blockHeight(_ p:ProviderUsage)->CGFloat {90+CGFloat(clientLines(p,width:clientWidth,font:clientFont).count)*17+CGFloat(max(1,p.models.count))*modelHeight}
    private var windowHeight:CGFloat {
        let chart=dashboard.dates.count>1 ? Self.chartHeight : 0
        let share:CGFloat=shared.isEmpty ? 0 : 56+CGFloat((shared.count+2)/3)*Self.legendRow
        return Self.titleBar+Self.pad*2+32+46+80+80+chart+share+rows.reduce(0){$0+Self.blockHeight($1)}+26
    }
    override var pictureHeight:CGFloat {Self.margin*2+windowHeight+58}
    // What the picture covers, written as dates: today, a 24-hour window, or first to last day.
    static func span(_ d:Dashboard)->String {
        func numbers(_ value:String)->[Int] {value.split(whereSeparator:{"-T:".contains($0)}).compactMap{Int($0)}}
        guard d.rangeKey != "today",let bounds=d.period,bounds.count==2 else{return d.date}
        let a=numbers(bounds[0]),b=numbers(bounds[1])
        guard a.count>=3,b.count==a.count else{return d.date}
        if a.count>=5 {return String(format:"%02d-%02d %02d:%02d → %02d-%02d %02d:%02d",a[1],a[2],a[3],a[4],b[1],b[2],b[3],b[4])}
        if a[0] != b[0] {return String(format:"%d-%02d-%02d → %d-%02d-%02d",a[0],a[1],a[2],b[0],b[1],b[2])}
        return a[1]==b[1] && a[2]==b[2] ? String(format:"%d-%02d-%02d",a[0],a[1],a[2]) : String(format:"%d-%02d-%02d → %02d-%02d",a[0],a[1],a[2],b[1],b[2])
    }
    // A terminal's colours, light or dark with the appearance.
    private struct Palette {let base,dots,window,bar,text,muted,comment,accent,rule:NSColor}
    private static func rgb(_ r:CGFloat,_ g:CGFloat,_ b:CGFloat,_ a:CGFloat=1)->NSColor {NSColor(srgbRed:r,green:g,blue:b,alpha:a)}
    private var palette:Palette {
        dark(self) ? Palette(base:Self.rgb(0.04,0.055,0.07),dots:Self.rgb(1,1,1,0.07),window:Self.rgb(0.06,0.08,0.10),bar:Self.rgb(0.09,0.11,0.14),
                             text:Self.rgb(0.90,0.93,0.95),muted:Self.rgb(0.62,0.66,0.70),comment:Self.rgb(0.45,0.50,0.55),accent:Self.rgb(0.25,0.85,0.72),rule:Self.rgb(1,1,1,0.10))
                   : Palette(base:Self.rgb(0.95,0.96,0.97),dots:Self.rgb(0,0,0,0.10),window:.white,bar:Self.rgb(0.95,0.96,0.97),
                             text:Self.rgb(0.11,0.13,0.15),muted:Self.rgb(0.36,0.40,0.44),comment:Self.rgb(0.52,0.56,0.60),accent:Self.rgb(0.02,0.54,0.46),rule:Self.rgb(0,0,0,0.09))
    }
    private func mono(_ size:CGFloat,_ weight:NSFont.Weight = .regular)->NSFont {NSFont.monospacedSystemFont(ofSize:size,weight:weight)}
    private func ui(_ size:CGFloat,_ weight:NSFont.Weight)->NSFont {NSFont.systemFont(ofSize:size,weight:weight)}
    // Large figures shrink to fit their slot rather than truncate.
    private func fitted(_ value:String,_ size:CGFloat,room:CGFloat)->NSFont {
        let measured=measure(value,mono(size,.semibold))
        return measured<=room ? mono(size,.semibold) : mono(max(11,floor(size*room/measured)),.semibold)
    }
    // Shares as a row of cells: each tool gets cells in proportion, at least one if it used anything.
    private static func cells(_ shares:[Double],count:Int)->[Int] {
        var result=shares.map{max($0>0 ? 1 : 0,Int(($0*Double(count)).rounded(.down)))}
        let order=shares.indices.sorted{shares[$0]*Double(count)-floor(shares[$0]*Double(count))>shares[$1]*Double(count)-floor(shares[$1]*Double(count))}
        var i=0
        while result.reduce(0,+)<count && !order.isEmpty {result[order[i%order.count]]+=1;i+=1}
        while result.reduce(0,+)>count,let widest=result.indices.max(by:{result[$0]<result[$1]}) {result[widest]-=1}
        return result
    }
    private func dashedRule(_ y:CGFloat,_ left:CGFloat,_ right:CGFloat,_ color:NSColor){
        let path=NSBezierPath();path.move(to:NSPoint(x:left,y:y));path.line(to:NSPoint(x:right,y:y))
        path.setLineDash([3,3],count:2,phase:0);path.lineWidth=1;color.setStroke();path.stroke()
    }
    private func prompt(_ command:String,x:CGFloat,middle:CGFloat,cursor:Bool,_ c:Palette){
        draw("❯",x:x,middle:middle,width:14,mono(12.5,.bold),c.accent)
        draw(command,x:x+16,middle:middle,width:Self.width,mono(12,.medium),c.text)
        if cursor {
            let after=x+16+measure(command,mono(12,.medium))+(command.isEmpty ? 0 : 6)
            c.accent.withAlphaComponent(0.85).setFill();NSRect(x:after,y:middle-7,width:7.5,height:14).fill()
        }
    }
    override func draw(_ dirtyRect:NSRect){
        let isDark=dark(self),c=palette
        // A dot grid, lit faintly behind the window.
        c.base.setFill();bounds.fill()
        c.dots.setFill()
        var gy:CGFloat=7
        while gy<bounds.height {var gx:CGFloat=7;while gx<bounds.width {NSRect(x:gx-0.6,y:gy-0.6,width:1.2,height:1.2).fill();gx+=14};gy+=14}
        let glow=NSPoint(x:bounds.width*0.15,y:60)
        NSGradient(starting:c.accent.withAlphaComponent(isDark ? 0.16 : 0.12),ending:c.accent.withAlphaComponent(0))?.draw(fromCenter:glow,radius:0,toCenter:glow,radius:bounds.width*0.9,options:[])
        // The window: title bar with its three lights, then the session.
        let window=NSRect(x:Self.margin,y:Self.margin,width:bounds.width-Self.margin*2,height:windowHeight),shape=NSBezierPath(roundedRect:window,xRadius:12,yRadius:12)
        NSGraphicsContext.saveGraphicsState()
        let shadow=NSShadow();shadow.shadowColor=NSColor.black.withAlphaComponent(isDark ? 0.55 : 0.14);shadow.shadowBlurRadius=34;shadow.shadowOffset=NSSize(width:0,height:-14);shadow.set()
        c.window.setFill();shape.fill()
        NSGraphicsContext.restoreGraphicsState()
        NSGraphicsContext.saveGraphicsState();shape.addClip()
        c.bar.setFill();NSRect(x:window.minX,y:window.minY,width:window.width,height:Self.titleBar).fill()
        NSGraphicsContext.restoreGraphicsState()
        line(NSPoint(x:window.minX,y:window.minY+Self.titleBar),NSPoint(x:window.maxX,y:window.minY+Self.titleBar),color:c.rule,width:1)
        let rim=NSBezierPath(roundedRect:window.insetBy(dx:0.5,dy:0.5),xRadius:11.5,yRadius:11.5);c.rule.setStroke();rim.lineWidth=1;rim.stroke()
        for (i,light) in [Self.rgb(1,0.37,0.34),Self.rgb(1,0.74,0.18),Self.rgb(0.16,0.78,0.25)].enumerated(){
            light.setFill();NSBezierPath(ovalIn:NSRect(x:window.minX+14+CGFloat(i)*18,y:window.minY+Self.titleBar/2-6,width:12,height:12)).fill()
        }
        draw("token-monitor — usage",x:window.minX,middle:window.minY+Self.titleBar/2,width:window.width,mono(11,.medium),c.muted,.center)
        let left=window.minX+Self.pad,width=window.width-Self.pad*2,right=left+width
        var y=window.minY+Self.titleBar+Self.pad
        let flag=["today":"today","24h":"24h","7d":"7d","30d":"30d","week":"week","month":"month","all":"all"][dashboard.rangeKey] ?? dashboard.rangeKey
        prompt("token-monitor usage --range "+flag,x:left,middle:y+9,cursor:false,c)
        y+=32
        // Title, and the dates it covers in a bracket-like tag.
        let span=Self.span(dashboard),spanFont=mono(10.5,.semibold),tag=NSRect(x:right-min(width*0.62,measure(span,spanFont)+18),y:y,width:min(width*0.62,measure(span,spanFont)+18),height:24)
        c.accent.withAlphaComponent(isDark ? 0.12 : 0.08).setFill();NSBezierPath(roundedRect:tag,xRadius:5,yRadius:5).fill()
        let tagRim=NSBezierPath(roundedRect:tag.insetBy(dx:0.5,dy:0.5),xRadius:4.5,yRadius:4.5);c.accent.withAlphaComponent(0.4).setStroke();tagRim.lineWidth=1;tagRim.stroke()
        draw(span,x:tag.minX+9,middle:tag.midY,width:tag.width-14,spanFont,c.accent)
        let title=Self.title(dashboard),room=tag.minX-left-10,full=measure(title,ui(19,.bold))
        draw(title,x:left,middle:tag.midY,width:room+2,ui(full<=room ? 19 : max(15,floor(19*room/full)),.bold),c.text)
        y+=46
        // The estimate, set like output, with the cursor after it.
        let counted=self.counted,costs=counted.compactMap{$0.cost},partial=counted.contains{$0.cost==nil || $0.unpriced>0}
        draw(partial ? "// 已知 API 等值估算" : "// API 等值估算",NSRect(x:left,y:y,width:width,height:15),mono(11),c.comment)
        let total=costs.isEmpty ? "—" : "$"+(Self.dollars.string(from:NSNumber(value:costs.reduce(0,+))) ?? "0.00")
        let heroFont=mono(min(42,floor(42*width*0.86/max(1,measure(total,mono(42,.bold))))),.bold),heroTop=y+18
        let hero=NSMutableAttributedString(string:total,attributes:[.font:heroFont,.foregroundColor:c.text,.kern:-1])
        if total.hasPrefix("$"){hero.addAttribute(.foregroundColor,value:c.accent,range:NSRange(location:0,length:1))}
        hero.draw(in:NSRect(x:left-1,y:heroTop,width:width,height:52))
        let heroWidth=hero.size().width
        c.accent.withAlphaComponent(0.85).setFill()
        NSRect(x:left+heroWidth+4,y:heroTop+heroFont.ascender-heroFont.capHeight-1,width:heroFont.pointSize*0.32,height:heroFont.capHeight+3).fill()
        y+=80
        // Three figures in a ruled table.
        let count=NumberFormatter();count.numberStyle = .decimal
        // Cache hit rate weighted by each tool's tokens; tools that report none are left out.
        let cached=counted.filter{$0.cache != nil},weight=cached.reduce(0){$0+$1.tokens}
        let hit=weight>0 ? String(format:"%.1f%%",cached.reduce(0){$0+($1.cache ?? 0)*$1.tokens}/weight*100) : "—"
        let figures=[("Token",tokens(counted.reduce(0){$0+$1.tokens})),("请求",count.string(from:NSNumber(value:counted.reduce(0){$0+$1.requests})) ?? "0"),("缓存命中",hit)]
        let table=NSRect(x:left,y:y,width:width,height:58),column=width/3
        c.bar.setFill();NSBezierPath(roundedRect:table,xRadius:7,yRadius:7).fill()
        let tableRim=NSBezierPath(roundedRect:table.insetBy(dx:0.5,dy:0.5),xRadius:6.5,yRadius:6.5);c.rule.setStroke();tableRim.lineWidth=1;tableRim.stroke()
        for (i,(label,value)) in figures.enumerated(){
            let x=table.minX+CGFloat(i)*column
            if i>0{line(NSPoint(x:x,y:table.minY),NSPoint(x:x,y:table.maxY),color:c.rule,width:1)}
            draw("▸ "+label,NSRect(x:x+12,y:table.minY+10,width:column-18,height:14),mono(10,.medium),c.muted)
            draw(value,NSRect(x:x+12,y:table.minY+28,width:column-18,height:24),fitted(value,18,room:column-22),c.text)
        }
        y+=80
        // Trend on an oscilloscope grid: the total glows, each tool is a thin line in its own colour.
        let n=dashboard.dates.count
        if n>1 {
            draw("// 用量趋势 · "+dashboard.chartUnit+" Token",NSRect(x:left,y:y,width:width,height:15),mono(10.5),c.comment)
            let series=counted.map{p in (0..<n).map{i in i<p.history.count ? max(0,p.history[i]) : 0}}
            // Same scale as the panel: a rounded ceiling with labelled half and full grid lines.
            let observed=max((0..<n).map{i in series.reduce(0){$0+$1[i]}}.max() ?? 0,1),unit=pow(10,floor(log10(observed)))
            let ceiling=ceil(observed*1.08/unit*2)/2*unit
            let plot=NSRect(x:left,y:y+34,width:width,height:104),axisFont=mono(9)
            for f in stride(from:CGFloat(0),through:1,by:0.2){
                let x=plot.minX+f*plot.width,grid=NSBezierPath();grid.move(to:NSPoint(x:x,y:plot.minY));grid.line(to:NSPoint(x:x,y:plot.maxY))
                grid.setLineDash([1,3],count:2,phase:0);grid.lineWidth=0.75;c.rule.setStroke();grid.stroke()
            }
            for f in [CGFloat(0),0.5,1]{
                let yy=plot.maxY-f*plot.height,grid=NSBezierPath();grid.move(to:NSPoint(x:plot.minX,y:yy));grid.line(to:NSPoint(x:plot.maxX,y:yy))
                if f>0 {grid.setLineDash([1,3],count:2,phase:0)};grid.lineWidth=f>0 ? 0.75 : 1;(f>0 ? c.rule : c.muted.withAlphaComponent(0.35)).setStroke();grid.stroke()
            }
            let positions=DashboardView.chartPositions(dashboard)
            let paths=DashboardView.smoothCurves(series,positions:positions,plot:plot,ceiling:ceiling),total=paths.last!
            let area=total.copy() as! NSBezierPath;area.line(to:NSPoint(x:plot.maxX,y:plot.maxY));area.line(to:NSPoint(x:plot.minX,y:plot.maxY));area.close()
            NSGraphicsContext.saveGraphicsState();area.addClip()
            NSGradient(starting:c.accent.withAlphaComponent(isDark ? 0.28 : 0.18),ending:c.accent.withAlphaComponent(0.0))?.draw(from:NSPoint(x:plot.midX,y:plot.minY),to:NSPoint(x:plot.midX,y:plot.maxY),options:[])
            NSGraphicsContext.restoreGraphicsState()
            for (i,curve) in paths.enumerated(){
                let isTotal=i==paths.count-1,colour=isTotal ? c.accent : accent(counted[i].id,self).withAlphaComponent(0.9)
                NSGraphicsContext.saveGraphicsState()
                let glowShadow=NSShadow();glowShadow.shadowColor=colour.withAlphaComponent(isTotal ? (isDark ? 0.8 : 0.45) : (isDark ? 0.5 : 0.0));glowShadow.shadowBlurRadius=isTotal ? 8 : 4;glowShadow.shadowOffset = .zero;glowShadow.set()
                curve.lineWidth=isTotal ? 2.2 : 1.2;curve.lineCapStyle = .round;curve.lineJoinStyle = .round;colour.setStroke();curve.stroke()
                NSGraphicsContext.restoreGraphicsState()
            }
            for f in [0.5,1.0]{
                let label=axisTokens(ceiling*f),yy=plot.maxY-CGFloat(f)*plot.height
                c.window.withAlphaComponent(0.85).setFill();NSRect(x:left,y:yy-13,width:measure(label,axisFont)+5,height:12).fill()
                draw(label,NSRect(x:left+1,y:yy-13,width:80,height:12),axisFont,c.comment)
            }
            let end=total.currentPoint
            c.accent.withAlphaComponent(0.22).setFill();NSBezierPath(ovalIn:NSRect(x:end.x-7,y:end.y-7,width:14,height:14)).fill()
            c.accent.setFill();NSBezierPath(ovalIn:NSRect(x:end.x-3,y:end.y-3,width:6,height:6)).fill()
            let middle=positions.indices.dropFirst().dropLast().min{abs(positions[$0]-0.5)<abs(positions[$1]-0.5)}
            for i in [0]+(middle.map{[$0]} ?? [])+[n-1]{
                let x=plot.minX+CGFloat(positions[i])*plot.width,align:NSTextAlignment=i==0 ? .left : (i==n-1 ? .right : .center)
                draw(dashboard.dates[i],NSRect(x:i==0 ? x : (i==n-1 ? x-80 : x-40),y:plot.maxY+7,width:80,height:13),mono(9.5),c.comment,align)
            }
            y+=Self.chartHeight
        }
        // Share of tokens as a row of cells, then a key that names each colour.
        let sum=max(counted.reduce(0){$0+$1.tokens},1),shared=self.shared
        if !shared.isEmpty {
            draw("// Token 占比",NSRect(x:left,y:y,width:width,height:15),mono(10.5),c.comment)
            let total=40,gap:CGFloat=2,cell=(width-gap*CGFloat(total-1))/CGFloat(total)
            var index=0
            for (p,n) in zip(shared,Self.cells(shared.map{$0.tokens/sum},count:total)){
                for _ in 0..<n {accent(p.id,self).setFill();NSBezierPath(roundedRect:NSRect(x:left+CGFloat(index)*(cell+gap),y:y+26,width:cell,height:10),xRadius:1.5,yRadius:1.5).fill();index+=1}
            }
            let column=width/3,keyFont=ui(11,.medium),shareFont=mono(10.5,.medium)
            for (i,p) in shared.enumerated(){
                let x=left+CGFloat(i%3)*column,ky=y+48+CGFloat(i/3)*Self.legendRow,percent=share(p.tokens/sum)
                accent(p.id,self).setFill();NSBezierPath(roundedRect:NSRect(x:x,y:ky+3.5,width:8,height:8),xRadius:2,yRadius:2).fill()
                let name=DashboardView.legendName(p),nameWidth=min(measure(name,keyFont),column-measure(percent,shareFont)-24)
                draw(name,x:x+13,middle:ky+7.5,width:nameWidth,keyFont,c.text)
                draw(percent,x:x+18+nameWidth,middle:ky+7.5,width:column-nameWidth-18,shareFont,c.muted)
            }
            y+=56+CGFloat((shared.count+2)/3)*Self.legendRow
        }
        // Each tool as a listing: cost, share and stats, then its models as a tree with cell bars.
        for p in rows {
            let colour=accent(p.id,self),inSummary=p.summaryIncluded != false,top=y
            dashedRule(top,left,right,c.rule)
            colour.withAlphaComponent(inSummary ? 1 : 0.4).setFill();NSBezierPath(ovalIn:NSRect(x:left,y:top+20,width:8,height:8)).fill()
            let nameFont=ui(14.5,.semibold),cost=amount(p),costFont=mono(14.5,.semibold),costWidth=measure(cost,costFont)
            let nameWidth=min(width*0.45,measure(p.name,nameFont))
            draw(p.name,x:left+16,middle:top+24,width:nameWidth+2,nameFont,c.text)
            // A source a gateway partly counts says so, as the panel card does: its share is only the rest.
            if !inSummary || (p.counted != nil && !p.subtitle.isEmpty) {
                let note=inSummary ? p.subtitle : "未计入 Summary",noteFont=mono(9.5,.medium)
                let room=right-costWidth-10-(left+24+nameWidth),noteWidth=min(room,measure(note,noteFont)+12)
                let box=NSRect(x:left+24+nameWidth,y:top+16,width:noteWidth,height:16)
                let boxPath=NSBezierPath(roundedRect:box.insetBy(dx:0.5,dy:0.5),xRadius:3.5,yRadius:3.5);c.muted.withAlphaComponent(0.45).setStroke();boxPath.lineWidth=1;boxPath.stroke()
                draw(note,x:box.minX+6,middle:box.midY,width:box.width-12,noteFont,c.muted,.center)
            }
            draw(cost,x:right-costWidth-2,middle:top+24,width:costWidth+2,costFont,c.text,.right)
            let part=p.summaryPart?.tokens ?? 0
            draw(tokenLine(p)+(part>0 ? " · 占 "+share(part/sum) : ""),NSRect(x:left+16,y:top+40,width:width-16,height:15),mono(10.5,.medium),c.muted)
            draw(DashboardView.stats(p),NSRect(x:left+16,y:top+57,width:width-16,height:15),mono(10.5),c.comment)
            var my=top+80
            for line in Self.clientLines(p,width:Self.clientWidth,font:Self.clientFont){draw(line,NSRect(x:left+16,y:my-6,width:width-16,height:15),Self.clientFont,c.comment);my+=17}
            if p.models.isEmpty{draw("此时段暂无模型调用",NSRect(x:left+16,y:my,width:width-16,height:15),mono(11),c.comment)}
            // Models hang off a drawn tree: one stem, a tick per model.
            if !p.models.isEmpty {
                let stem=left+20,branch=c.comment.withAlphaComponent(0.7)
                line(NSPoint(x:stem,y:my+1),NSPoint(x:stem,y:my+8+CGFloat(p.models.count-1)*Self.modelHeight),color:branch,width:1)
                for i in p.models.indices {let mid=my+8+CGFloat(i)*Self.modelHeight;line(NSPoint(x:stem,y:mid),NSPoint(x:stem+10,y:mid),color:branch,width:1)}
            }
            // Columns sized to this tool's longest model name and amount; the cells take what is left.
            let modelFont=mono(11),figureFont=mono(11,.medium)
            let nameColumn=min(140,(p.models.map{measure(modelName($0.name),modelFont)}.max() ?? 0)+12)
            let amountColumn=max(50,(p.models.map{measure(modelAmount($0),figureFont)}.max() ?? 0)+4)
            let barLeft=left+40+nameColumn,barRoom=right-44-amountColumn-10-barLeft,slots=max(4,min(16,Int(barRoom/6.5)))
            for m in p.models{
                draw(modelName(m.name),x:left+38,middle:my+8,width:nameColumn-8,modelFont,c.text)
                let lit=m.share>0 ? max(1,Int((min(1,m.share)*Double(slots)).rounded())) : 0
                for k in 0..<slots {
                    (k<lit ? colour.withAlphaComponent(inSummary ? 0.95 : 0.5) : c.rule).setFill()
                    NSBezierPath(roundedRect:NSRect(x:barLeft+CGFloat(k)*6.5,y:my+4,width:5,height:8),xRadius:1,yRadius:1).fill()
                }
                draw(modelAmount(m),x:right-44-amountColumn,middle:my+8,width:amountColumn,figureFont,c.text,.right)
                draw(share(m.share),x:right-40,middle:my+8,width:40,mono(10.5),c.muted,.right)
                my+=Self.modelHeight
            }
            y+=Self.blockHeight(p)
        }
        // The session waits at a prompt.
        dashedRule(y,left,right,c.rule)
        prompt("",x:left,middle:y+16,cursor:true,c)
        // Signature under the window: the mark, the name and where to find it.
        let footer=window.maxY+18,signFont=ui(13.5,.semibold),signWidth=measure(project.name,signFont),rowWidth=20+8+signWidth
        brandMark(NSRect(x:bounds.midX-rowWidth/2,y:footer,width:20,height:20))
        draw(project.name,x:bounds.midX-rowWidth/2+28,middle:footer+10,width:signWidth+2,signFont,c.text)
        draw(project.url,NSRect(x:0,y:footer+26,width:bounds.width,height:14),mono(10),c.comment,.center)
    }
}
final class DashboardView:FlippedView {
    var data:Dashboard;var toggleProvider:((String,Bool)->Void)?;private var hoveredProvider:String?
    private var hoveredDay:Int?;private var calendarTracking:NSTrackingArea?
    private var summaryProviders:[ProviderUsage] {data.providers.compactMap{$0.summaryPart}}
    static func sectionHeight(_ p:ProviderUsage)->CGFloat {78+clientHeight(p)+CGFloat(max(1,p.models.count))*18}
    // A gateway's clients in one line, by their share of its tokens: "客户端 · ChatGPT 62% · Claude Code 30%".
    static func clientLine(_ p:ProviderUsage)->String? {
        guard p.available,let clients=p.clients,!clients.isEmpty else{return nil}
        return "客户端 · "+clients.map{$0.name+" "+share($0.share)}.joined(separator:" · ")
    }
    static func clientHeight(_ p:ProviderUsage)->CGFloat {clientLine(p)==nil ? 0 : 16}
    static func providerHeight(_ p:ProviderUsage,_ d:Dashboard)->CGFloat {
        guard d.allowCollapse==true else{return sectionHeight(p)}
        return (d.expandedProviders ?? []).contains(p.id) ? 74+clientHeight(p)+CGFloat(max(1,p.models.count))*18 : 52
    }
    // Legend names drop suffixes the cards below already show, so four fit on a row.
    static func legendLabels(_ d:Dashboard)->[String] {["总量"]+d.providers.compactMap{$0.summaryPart}.map(legendName)}
    static func legendName(_ p:ProviderUsage)->String {
        p.id=="claude" ? "Claude" : p.name.hasSuffix(" Harness") ? String(p.name.dropLast(" Harness".count)) : p.name
    }
    // The dashboard is always 344pt wide, so the legend gets 320pt between the margins.
    static func chartExtra(_ d:Dashboard)->CGFloat {CGFloat(max(0,legendLayout(legendLabels(d),width:320).count-1))*17}
    static func summaryHeight(_ d:Dashboard)->CGFloat {d.showSummary==false ? 44 : 230+chartExtra(d)}
    static func harnessHeaderHeight(_ d:Dashboard)->CGFloat {d.providers.isEmpty || d.showSummary==false ? 0 : 6}
    static func height(_ d:Dashboard)->CGFloat {summaryHeight(d)+calendarHeight(d)+harnessHeaderHeight(d)+d.providers.reduce(0){$0+providerHeight($1,d)}+(d.providers.isEmpty ? 0 : 4)}
    static func totalColor(_ view:NSView)->NSColor {dark(view) ? NSColor(srgbRed:0.43,green:0.79,blue:0.75,alpha:1) : NSColor(srgbRed:0.15,green:0.47,blue:0.46,alpha:1)}
    // The calendar sits under Summary: its figures on the left, the days on the right up to this week.
    static let calendarPitch:CGFloat=9,calendarGap:CGFloat=1.5,calendarDayColumn:CGFloat=12,calendarTextWidth:CGFloat=100
    static func calendarHeight(_ d:Dashboard)->CGFloat {d.calendar==nil ? 0 : 90}
    static func calendarGrid(_ d:Dashboard,width:CGFloat)->NSRect {
        let w=CGFloat(d.calendar?.shownWeeks ?? 0)*calendarPitch-calendarGap
        return NSRect(x:width-12-w,y:summaryHeight(d)+16,width:w,height:7*calendarPitch-calendarGap)
    }
    // Where the figures' rows end: clear of the day names beside the grid.
    static func calendarInfoRight(_ d:Dashboard,width:CGFloat)->CGFloat {calendarGrid(d,width:width).minX-calendarDayColumn-16}
    // The day under a point, or nil outside the grid and after today.
    static func calendarDay(at point:NSPoint,_ d:Dashboard,width:CGFloat)->Int? {
        guard let c=d.calendar else{return nil}
        let grid=calendarGrid(d,width:width)
        guard grid.insetBy(dx:-1,dy:-1).contains(point) else{return nil}
        let week=min(c.shownWeeks-1,max(0,Int((point.x-grid.minX)/calendarPitch))),row=min(6,max(0,Int((point.y-grid.minY)/calendarPitch)))
        let index=c.firstShown+week*7+row
        return index<c.tokens.count ? index : nil
    }
    static func heading(_ d:Dashboard)->String {d.showSummary==false ? "Harness 用量" : "Summary"}
    // The heading ends clear of the buttons beside it; the leftmost one (自动检测) starts 209pt from the right.
    static func headingWidth(_ width:CGFloat)->CGFloat {width-209-18}
    init(_ data:Dashboard,width:CGFloat){
        self.data=data;super.init(frame:NSRect(x:0,y:0,width:width,height:Self.height(data)))
        setAccessibilityElement(true);setAccessibilityRole(.group);setAccessibilityLabel("用量概览")
        if data.allowCollapse==true {
            var y=Self.summaryHeight(data)+Self.calendarHeight(data)+Self.harnessHeaderHeight(data)
            for p in data.providers {
                let expanded=(data.expandedProviders ?? []).contains(p.id)
                let button=DisclosureButton(label:(expanded ? "收起 " : "展开 ")+p.name+" 明细",frame:NSRect(x:4,y:y+2,width:width-8,height:46))
                button.setAccessibilityValue(expanded ? "已展开" : "已折叠")
                button.invoke={[weak self] in self?.toggleProvider?(p.id,!expanded)}
                button.hovered={[weak self] over in self?.hoveredProvider=over ? p.id : nil;self?.needsDisplay=true}
                addSubview(button);y+=Self.providerHeight(p,data)
            }
        }
    }

    required init?(coder:NSCoder){fatalError()}
    override func viewDidChangeEffectiveAppearance(){super.viewDidChangeEffectiveAppearance();needsDisplay=true}
    // Pointing at a day reads it out on the calendar's own figures line.
    override func updateTrackingAreas(){
        super.updateTrackingAreas()
        if let calendarTracking{removeTrackingArea(calendarTracking)}
        calendarTracking=nil
        guard data.calendar != nil else{return}
        let area=NSTrackingArea(rect:Self.calendarGrid(data,width:bounds.width).insetBy(dx:-2,dy:-2),options:[.mouseMoved,.mouseEnteredAndExited,.activeAlways],owner:self)
        addTrackingArea(area);calendarTracking=area
    }
    override func mouseMoved(with event:NSEvent){hoverDay(Self.calendarDay(at:convert(event.locationInWindow,from:nil),data,width:bounds.width))}
    override func mouseEntered(with event:NSEvent){mouseMoved(with:event)}
    override func mouseExited(with event:NSEvent){hoverDay(nil)}
    private func hoverDay(_ day:Int?){
        guard day != hoveredDay else{return}
        hoveredDay=day;setNeedsDisplay(NSRect(x:0,y:Self.summaryHeight(data),width:bounds.width,height:Self.calendarHeight(data)))
    }
    override func draw(_ dirtyRect:NSRect){
        let left:CGFloat=12,right=bounds.width-12,width=right-left
        let valid=summaryProviders.filter{$0.available},costs=valid.compactMap{$0.cost}
        text(Self.heading(data),x:left,y:10,w:Self.headingWidth(bounds.width),size:14,weight:.semibold)
        if data.showSummary != false {
        let partial=valid.count != summaryProviders.count || valid.contains{$0.unpriced>0}
        let labels=[partial ? "已知 API 估算" : "API 估算","Token 用量","请求"]
        let values=[money(costs.isEmpty ? nil : costs.reduce(0,+)),valid.isEmpty ? "—" : tokens(valid.reduce(0){$0+$1.tokens}),valid.isEmpty ? "—" : String(valid.reduce(0){$0+$1.requests})]
        // Justified like the legend: left, centre and right thirds, each label above its value.
        // Each cell keeps 4pt clear of its neighbours: the first hugs the left edge, the last the right.
        let third=width/3,alignments:[NSTextAlignment]=[.left,.center,.right]
        for i in 0..<3 {let x=left+third*CGFloat(i)+2*CGFloat(i);text(labels[i],x:x,y:40,w:third-4,size:10,color:.secondaryLabelColor,align:alignments[i]);text(values[i],x:x,y:56,w:third-4,size:i==2 ? 20 : 21,weight:.medium,align:alignments[i],numeric:true)}
        drawSummaryChart(left:left,right:right)
        }
        var y:CGFloat=Self.summaryHeight(data)
        if let calendar=data.calendar {drawCalendar(calendar,left:left,right:right);y+=Self.calendarHeight(data)}
        y+=Self.harnessHeaderHeight(data)
        for p in data.providers {if data.allowCollapse==true{drawCollapsibleProvider(p,y:y,left:left,right:right)}else{drawProvider(p,y:y,left:left,right:right)};y+=Self.providerHeight(p,data)}
    }
    static func calendarShades(_ view:NSView)->[NSColor] {
        let total=totalColor(view)
        return [NSColor.labelColor.withAlphaComponent(dark(view) ? 0.075 : 0.06)]+[0.3,0.52,0.76,1].map{total.withAlphaComponent($0)}
    }
    private func drawCalendar(_ c:UsageCalendar,left:CGFloat,right:CGFloat){
        let grid=Self.calendarGrid(data,width:bounds.width),pitch=Self.calendarPitch,cell=pitch-Self.calendarGap
        let shades=Self.calendarShades(self),levels=c.levels,first=c.firstShown
        for (week,label) in c.months {text(label,x:grid.minX+CGFloat(week)*pitch,y:grid.minY-14,w:30,size:8,color:.secondaryLabelColor)}
        for (row,label) in [(0,"一"),(2,"三"),(4,"五")] {text(label,x:grid.minX-Self.calendarDayColumn,y:grid.minY+CGFloat(row)*pitch+cell/2-7,w:Self.calendarDayColumn-3,size:8,color:.secondaryLabelColor,align:.right)}
        for week in 0..<c.shownWeeks {
            for row in 0..<7 {
                let index=first+week*7+row
                guard index<levels.count else{continue}
                let rect=NSRect(x:grid.minX+CGFloat(week)*pitch,y:grid.minY+CGFloat(row)*pitch,width:cell,height:cell)
                shades[levels[index]].setFill();NSBezierPath(roundedRect:rect,xRadius:1.6,yRadius:1.6).fill()
                if index==hoveredDay {
                    let ring=NSBezierPath(roundedRect:rect.insetBy(dx:-1,dy:-1),xRadius:2.2,yRadius:2.2);ring.lineWidth=1.2
                    NSColor.labelColor.withAlphaComponent(0.6).setStroke();ring.stroke()
                }
            }
        }
        // Beside the grid, rows reach from the left edge to the day names, label left and value right, so a short
        // history leaves no hole. They hold the figures, or the day pointed at; the shade key, or that day's date,
        // sits along the grid's bottom edge.
        let top=grid.minY-3,infoRight=Self.calendarInfoRight(data,width:bounds.width),bottom=grid.maxY-cell
        let pointed=hoveredDay.map{c.detail($0)},rows=pointed?.rows ?? (c.note==nil ? c.figures : [])
        if pointed==nil,let note=c.note {text(note,x:left,y:top,w:infoRight-left,size:10,color:.secondaryLabelColor)}
        for (i,row) in rows.enumerated() {
            let y=top+CGFloat(i)*16
            text(row.label,x:left,y:y+1.5,w:44,size:9.5,color:.secondaryLabelColor)
            text(row.value,x:left+44,y:y,w:infoRight-left-44,size:11,weight:.medium,align:.right,numeric:true)
            if i<rows.count-1 {line(NSPoint(x:left,y:y+15.5),NSPoint(x:infoRight,y:y+15.5),color:NSColor.separatorColor.withAlphaComponent(0.35),width:0.5)}
        }
        if let pointed {text(pointed.date,x:left,y:bottom-4,w:infoRight-left,size:9.5,color:.secondaryLabelColor);return}
        text("少",x:left,y:bottom-3,w:12,size:8,color:.secondaryLabelColor)
        for (i,shade) in shades.enumerated() {shade.setFill();NSBezierPath(roundedRect:NSRect(x:left+12+CGFloat(i)*pitch,y:bottom,width:cell,height:cell),xRadius:1.6,yRadius:1.6).fill()}
        text("多",x:left+14+5*pitch,y:bottom-3,w:12,size:8,color:.secondaryLabelColor)
    }
    // Normal states say nothing; the slot shows a problem, or a note that changes how to read the numbers.
    static func note(_ p:ProviderUsage)->(String,Bool)? {
        if !p.available {return (p.status=="运行正常" || p.status=="已同步" ? "数据暂不可用" : p.status,true)}
        if p.status != "运行正常" && p.status != "已同步" {return (p.status,true)}
        return p.subtitle.isEmpty ? nil : (p.subtitle,false)
    }
    static func stats(_ p:ProviderUsage)->String {
        guard p.available else{return "等待可用数据"}
        // Cache and output say nothing when only Credits were recorded.
        if p.tokens==0,p.credits != nil {return "\(p.requests) 请求 · \(p.sessions) 会话"}
        var parts=["\(p.requests) 请求",p.cache.map{String(format:"缓存 %.1f%%",$0*100)} ?? "缓存 —","输出 \(tokens(p.output))"]
        if p.id=="opencodex"{if let coverage=p.coverage,coverage<1{parts.append(String(format:"已定价 %.0f%%",coverage*100))}}else{parts.append("\(p.sessions) 会话")}
        if p.cost != nil,let credits=p.credits {parts.append(creditsText(credits))}
        return parts.joined(separator:" · ")
    }
    private func drawNote(_ p:ProviderUsage,y:CGFloat,right:CGFloat){
        guard let (note,warning)=Self.note(p) else{return}
        text(note,x:right-150,y:y,w:133,size:9,color:warning ? .systemOrange : .secondaryLabelColor,align:.right)
    }
    private func drawModels(_ p:ProviderUsage,y:CGFloat,left:CGFloat,right:CGFloat,color c:NSColor){
        let width=right-left
        if p.models.isEmpty{text(p.available ? "此时段暂无模型调用" : "模型明细暂不可用",x:left,y:y,w:width,size:10,color:.secondaryLabelColor)}
        for (i,m) in p.models.enumerated(){
            let top=y+CGFloat(i)*18
            text(modelName(m.name),x:left,y:top,w:width-120,size:10.5)
            text(modelAmount(m),x:right-115,y:top,w:72,size:10.5,align:.right,numeric:true)
            text(share(m.share),x:right-38,y:top,w:38,size:10,color:.secondaryLabelColor,align:.right,numeric:true)
            NSColor.quaternaryLabelColor.withAlphaComponent(0.12).setFill();NSRect(x:left,y:top+14,width:width,height:1.5).fill()
            c.withAlphaComponent(0.55).setFill();NSRect(x:left,y:top+14,width:width*CGFloat(m.share),height:1.5).fill()
        }
    }
    private func drawCollapsibleProvider(_ p:ProviderUsage,y:CGFloat,left:CGFloat,right:CGFloat){
        let width=right-left,c=accent(p.id,self),expanded=(data.expandedProviders ?? []).contains(p.id)
        if hoveredProvider==p.id {NSColor.controlAccentColor.withAlphaComponent(dark(self) ? 0.08 : 0.045).setFill();NSBezierPath(roundedRect:NSRect(x:4,y:y+2,width:bounds.width-8,height:46),xRadius:7,yRadius:7).fill()}
        c.setFill();NSBezierPath(ovalIn:NSRect(x:left,y:y+12,width:5,height:5)).fill()
        text(p.name,x:left+12,y:y+6,w:width-170,size:12,weight:.semibold)
        drawNote(p,y:y+8,right:right)
        let arrow=NSBezierPath(),x=right-5,cy=y+14
        arrow.move(to:NSPoint(x:x-3,y:cy-3));arrow.line(to:NSPoint(x:expanded ? x : x+0.5,y:cy));arrow.line(to:NSPoint(x:expanded ? x+3 : x-3,y:expanded ? cy-3 : cy+3))
        arrow.lineWidth=1.2;arrow.lineCapStyle = .round;arrow.lineJoinStyle = .round;NSColor.secondaryLabelColor.setStroke();arrow.stroke()
        text(p.available ? headline(p) : "—",x:left+12,y:y+26,w:100,size:14,weight:.medium,numeric:true)
        text(p.available ? tokenLine(p) : "—",x:left+120,y:y+27,w:width-120,size:12,weight:.medium,align:.right,numeric:true)
        if expanded {
            text(Self.stats(p),x:left+12,y:y+50,w:width-12,size:10,color:.secondaryLabelColor)
            if let clients=Self.clientLine(p){text(clients,x:left+12,y:y+66,w:width-12,size:10,color:.secondaryLabelColor)}
            drawModels(p,y:y+70+Self.clientHeight(p),left:left+12,right:right,color:c)
        }
    }
    private func drawProvider(_ p:ProviderUsage,y:CGFloat,left:CGFloat,right:CGFloat){
        let width=right-left,c=accent(p.id,self)
        c.setFill();NSBezierPath(ovalIn:NSRect(x:left,y:y+14,width:5,height:5)).fill()
        text(p.name,x:left+12,y:y+8,w:width-170,size:12,weight:.semibold)
        drawNote(p,y:y+10,right:right+17)
        text(p.available ? headline(p) : "—",x:left,y:y+26,w:width*0.43,size:19,weight:.medium,numeric:true)
        text(p.available ? tokenLine(p) : "—",x:left+width*0.43,y:y+28,w:width*0.57,size:16,weight:.medium,align:.right,numeric:true)
        text(Self.stats(p),x:left,y:y+54,w:width,size:10,color:.secondaryLabelColor)
        if let clients=Self.clientLine(p){text(clients,x:left,y:y+70,w:width,size:10,color:.secondaryLabelColor)}
        drawModels(p,y:y+74+Self.clientHeight(p),left:left,right:right,color:c)
    }
    // Legend columns are justified: the first starts at the left edge, the last column's longest
    // label ends at the right edge, and column starts are evenly spaced. Four per row when they
    // fit, otherwise three, with rows as even as possible.
    static func legendLayout(_ labels:[String],width:CGFloat)->[[NSRect]] {
        guard !labels.isEmpty else{return []}
        let font=NSFont.systemFont(ofSize:8.5),gap:CGFloat=6
        let sizes=labels.map{ceil(($0 as NSString).size(withAttributes:[.font:font]).width)+16}
        func shape(_ most:Int)->(count:Int,pitch:CGFloat){
            let rows=(labels.count+most-1)/most,count=(labels.count+rows-1)/rows
            let last=stride(from:count-1,to:sizes.count,by:count).map{sizes[$0]}.max() ?? 0
            return (count,count>1 ? (width-last)/CGFloat(count-1) : 0)
        }
        func fits(_ s:(count:Int,pitch:CGFloat))->Bool{sizes.enumerated().allSatisfy{$0.offset%s.count==s.count-1 || $0.element+gap<=s.pitch}}
        var s=shape(4)
        if !fits(s){s=shape(3)}
        if !fits(s){s.pitch=width/CGFloat(s.count)}
        return stride(from:0,to:labels.count,by:s.count).map{start in
            (0..<min(s.count,labels.count-start)).map{c in
                let x=CGFloat(c)*s.pitch
                return NSRect(x:x,y:0,width:c==s.count-1 ? width-x : s.pitch-gap,height:12)
            }
        }
    }
    static func chartPositions(_ d:Dashboard)->[Double] {
        let n=d.dates.count
        if let values=d.chartPositions,values.count==n,values.allSatisfy({$0.isFinite}),zip(values,values.dropFirst()).allSatisfy({$0<$1}) {return values}
        return (0..<n).map{n>1 ? Double($0)/Double(n-1) : 0.5}
    }
    // Smoothed curves for each series followed by their total. Slopes and limiters are shared,
    // so the smoothed total equals the sum of the provider curves at every point, including
    // between days, and no line overshoots its observations. Needs at least two points.
    static func smoothCurves(_ series:[[Double]],positions:[Double],plot:NSRect,ceiling:Double)->[NSBezierPath] {
        let n=positions.count
        let totals=(0..<n).map{i in series.reduce(0){$0+$1[i]}}
        let intervals=(0..<n-1).map{positions[$0+1]-positions[$0]}
        func slopes(_ values:[Double])->[Double]{
            let d=(0..<n-1).map{(values[$0+1]-values[$0])/intervals[$0]}
            var result=[Double](repeating:0,count:n)
            result[0]=d[0];result[n-1]=d[n-2]
            if n>2{for i in 1..<n-1{result[i]=(d[i-1]+d[i])/3}}
            return result
        }
        let shapeSeries=series+[totals]
        var tangents=shapeSeries.map{slopes($0)}
        for i in 0..<n {
            var factor=1.0
            for (j,values) in shapeSeries.enumerated(){
                let t=tangents[j][i]
                for k in [i-1,i] where k>=0 && k<n-1 {
                    let d=(values[k+1]-values[k])/intervals[k]
                    if d==0 || t*d<0{factor=0}else if abs(t)>0{factor=min(factor,abs(3*d/t))}
                }
            }
            for j in tangents.indices{tangents[j][i]*=factor}
        }
        for i in 0..<n-1 {
            var factor=1.0
            for (j,values) in shapeSeries.enumerated(){let delta=values[i+1]-values[i];if delta != 0 {let a=tangents[j][i]*intervals[i]/delta,b=tangents[j][i+1]*intervals[i]/delta,s=a*a+b*b;if s>9{factor=min(factor,3/sqrt(s))}}}
            for j in tangents.indices{tangents[j][i]*=factor;tangents[j][i+1]*=factor}
        }
        let scale=plot.height/CGFloat(ceiling)
        return zip(shapeSeries,tangents).map{values,slopes in
            let points=values.enumerated().map{NSPoint(x:plot.minX+CGFloat(positions[$0.offset])*plot.width,y:plot.maxY-CGFloat($0.element)*scale)}
            let curve=NSBezierPath();curve.move(to:points[0])
            for i in 0..<n-1{let dt=CGFloat(intervals[i]),dx=plot.width*dt;curve.curve(to:points[i+1],controlPoint1:NSPoint(x:points[i].x+dx/3,y:points[i].y-CGFloat(slopes[i])*dt*scale/3),controlPoint2:NSPoint(x:points[i+1].x-dx/3,y:points[i+1].y+CGFloat(slopes[i+1])*dt*scale/3))}
            return curve
        }
    }
    private func drawSummaryChart(left:CGFloat,right:CGFloat){
        let width=right-left,n=data.dates.count
        guard n>0 else{return}
        let series=summaryProviders.map{p in (0..<n).map{i in i<p.history.count ? max(0,p.history[i]) : 0}}
        let totals=(0..<n).map{i in series.reduce(0){$0+$1[i]}}
        let totalColor=Self.totalColor(self)
        let labels=Self.legendLabels(data),colors=[totalColor]+summaryProviders.map{accent($0.id,self)}
        var index=0
        for (row,frame) in Self.legendLayout(labels,width:width).enumerated(){
            for cell in frame{
                let x=left+cell.minX,y=CGFloat(row)*17
                colors[index].setFill();NSBezierPath(roundedRect:NSRect(x:x,y:96+y,width:11,height:index==0 ? 3 : 2),xRadius:1.5,yRadius:1.5).fill()
                text(labels[index],x:x+16,y:90+y,w:cell.width-16,size:8.5,color:.secondaryLabelColor)
                index+=1
            }
        }
        NSGraphicsContext.saveGraphicsState()
        defer{NSGraphicsContext.restoreGraphicsState()}
        NSGraphicsContext.current?.cgContext.translateBy(x:0,y:Self.chartExtra(data))
        let observed=max(totals.max() ?? 0,1),unit=pow(10,floor(log10(observed)))
        let ceiling=ceil(observed*1.08/unit*2)/2*unit
        // The chart's text keeps to the same edges as the figures above: axis labels flush left, the card
        // right after the widest of them, and the note and last time label flush right.
        let axis=[CGFloat(0),0.5,1].map{axisTokens(ceiling*Double($0))},axisFont=NSFont.monospacedDigitSystemFont(ofSize:8,weight:.regular)
        let axisWidth=axis.map{ceil(($0 as NSString).size(withAttributes:[.font:axisFont]).width)}.max() ?? 0
        let cardFrame=NSRect(x:left+axisWidth+5,y:119,width:right-left-axisWidth-5,height:90),card=NSBezierPath(roundedRect:cardFrame,xRadius:7,yRadius:7)
        let plot=NSRect(x:cardFrame.minX+5,y:127,width:cardFrame.width-9,height:78)
        totalColor.withAlphaComponent(dark(self) ? 0.035 : 0.025).setFill();card.fill()
        for (f,label) in zip([CGFloat(0),0.5,1],axis){
            let yy=plot.maxY-f*plot.height
            text(label,x:left,y:yy-5,w:axisWidth+1,size:8,color:.secondaryLabelColor,numeric:true)
            line(NSPoint(x:plot.minX,y:yy),NSPoint(x:plot.maxX,y:yy),color:NSColor.separatorColor.withAlphaComponent(0.22),width:0.5)
        }
        // Partial data and when the figures were read share a faint note just above the card, clear of its edge.
        let note=[data.chartStatus,data.updated ?? ""].filter{!$0.isEmpty}.joined(separator:" · ")
        if !note.isEmpty {text(note,x:cardFrame.minX,y:cardFrame.minY-12,w:cardFrame.width,size:7.5,color:.tertiaryLabelColor,align:.right)}
        if n==1 {
            for (i,values) in series.enumerated(){let y=plot.maxY-CGFloat(values[0]/ceiling)*plot.height;accent(summaryProviders[i].id,self).setFill();NSBezierPath(ovalIn:NSRect(x:plot.midX-2.5,y:y-2.5,width:5,height:5)).fill()}
            let y=plot.maxY-CGFloat(totals[0]/ceiling)*plot.height;totalColor.setFill();NSBezierPath(ovalIn:NSRect(x:plot.midX-3,y:y-3,width:6,height:6)).fill()
            text(data.dates[0],x:plot.midX-30,y:212,w:60,size:8.5,color:.secondaryLabelColor,align:.center);return
        }
        let positions=Self.chartPositions(data)
        let paths=Self.smoothCurves(series,positions:positions,plot:plot,ceiling:ceiling),totalPath=paths.last!
        let area=totalPath.copy() as! NSBezierPath;area.line(to:NSPoint(x:plot.maxX,y:plot.maxY));area.line(to:NSPoint(x:plot.minX,y:plot.maxY));area.close()
        NSGraphicsContext.saveGraphicsState();area.addClip();NSGradient(starting:totalColor.withAlphaComponent(dark(self) ? 0.10 : 0.07),ending:totalColor.withAlphaComponent(0.005))?.draw(from:NSPoint(x:plot.midX,y:plot.minY),to:NSPoint(x:plot.midX,y:plot.maxY),options:[]);NSGraphicsContext.restoreGraphicsState()
        for (i,curve) in paths.enumerated(){
            let total=i==paths.count-1
            curve.lineWidth=total ? 2.0 : 1.45;curve.lineCapStyle = .round;curve.lineJoinStyle = .round
            (total ? totalColor : accent(summaryProviders[i].id,self)).setStroke();curve.stroke()
        }
        let middle=(1..<n-1).filter{positions[$0]>=0.25 && positions[$0]<=0.75}.min{abs(positions[$0]-0.5)<abs(positions[$1]-0.5)}
        let labelIndices=[0]+(middle.map{[$0]} ?? [])+[n-1]
        for i in labelIndices{let label=data.dates[i];let x=plot.minX+CGFloat(positions[i])*plot.width;text(label,x:i==0 ? x : (i==n-1 ? right-48 : x-25),y:212,w:i==n-1 ? 48 : 50,size:8.5,color:.secondaryLabelColor,align:i==0 ? .left : (i==n-1 ? .right : .center))}
    }

}
enum MenuLayout {
    static func configurationClick(_ e:Entry,x:CGFloat,width:CGFloat)->Bool {
        e.isModule && !e.children.isEmpty && x>=width-32 && x<=width
    }
    static func configurationHover(_ e:Entry,x:CGFloat)->Bool { !e.isModule || x>=36 }
    static func hasIcon(_ e:Entry)->Bool {e.image != nil || e.symbol != nil || e.checked != nil || e.statusLight != nil}
    static func column(_ entries:[Entry])->CGFloat {entries.contains(where:hasIcon) ? 36 : 12}
    static func labelFont(_ e:Entry)->NSFont {.systemFont(ofSize:e.section==true ? 10.5 : (e.actionable ? 12 : 10.5),weight:e.section==true ? .medium : .regular)}
    static let valueFont=NSFont.monospacedDigitSystemFont(ofSize:11,weight:.regular)
    static let detailFont=NSFont.systemFont(ofSize:10)
    static func width(_ value:String,font:NSFont)->CGFloat {ceil((value as NSString).size(withAttributes:[.font:font]).width)}
    static func height(_ value:String,font:NSFont,width:CGFloat)->CGFloat {
        ceil((value as NSString).boundingRect(with:NSSize(width:max(1,width),height:1000),options:[.usesLineFragmentOrigin,.usesFontLeading],attributes:[.font:font]).height)
    }
    static func configured(_ value:String?)->Bool? {
        if value=="已配置"{return true}
        if value=="未配置"{return false}
        return nil
    }
    static func valueWidth(_ e:Entry)->CGFloat {e.value.map{width($0,font:valueFont)+(e.attention==true ? 16 : configured($0) != nil ? 12 : 0)} ?? 0}
    static func trailing(_ e:Entry)->CGFloat {e.children.isEmpty ? 12 : 32}
    static func labelWidth(_ e:Entry,width:CGFloat,left:CGFloat)->CGFloat {max(24,width-left-trailing(e)-valueWidth(e)-(e.value == nil ? 0 : 10))}
    // A value too wide for its row gives way to its shorter forms, so neither the title nor the value is cut.
    static func fitted(_ e:Entry,width:CGFloat,left:CGFloat)->Entry {
        guard let value=e.value,let shorter=e.fallbacks,!shorter.isEmpty else{return e}
        let room=width-left-trailing(e)-10-MenuLayout.width(e.label,font:labelFont(e));var entry=e
        for form in [value]+shorter {entry.value=form;if valueWidth(entry)<=room{break}}
        return entry
    }
    static func rowHeight(_ e:Entry,width:CGFloat,left:CGFloat)->CGFloat {
        let e=fitted(e,width:width,left:left)
        if e.separator==true{return 12}
        if e.section==true{return 25}
        let titleHeight=height(e.label,font:labelFont(e),width:labelWidth(e,width:width,left:left))
        let detailHeight=e.detail.map{height($0,font:detailFont,width:width-left-trailing(e))+3} ?? 0
        return max(e.actionable ? 27 : 21,titleHeight+detailHeight+(e.detail == nil ? 8 : 12))
    }
    static func flyoutWidth(_ entry:Entry)->CGFloat {
        let left=column(entry.children)
        var needed=width(entry.label,font:.systemFont(ofSize:11,weight:.medium))+left+28
        for e in entry.children where e.separator != true {
            let title=width(e.label,font:labelFont(e))+valueWidth(e)+(e.value == nil ? 0 : 10)
            let detail=e.detail.map{min(216,width($0,font:detailFont))} ?? 0
            needed=max(needed,max(title,detail)+left+trailing(e)+16)
        }
        return min(296,max(224,ceil(needed/8)*8))
    }
    // Prefer the same side as the previous level; never bounce back over the root if there is room.
    static func flyoutX(parent:NSRect,ancestor:NSRect?,width:CGFloat,screen:NSRect)->CGFloat {
        let left=parent.minX-width-5,right=parent.maxX+5
        let preferLeft=ancestor.map{parent.minX<$0.minX} ?? false
        let candidates=preferLeft ? [left,right] : [right,left]
        if let fits=candidates.first(where:{$0>=screen.minX+4 && $0+width<=screen.maxX-4}){return fits}
        return max(screen.minX+4,min(candidates[0],screen.maxX-width-4))
    }
    static func topmostIndex(at point:NSPoint,frames:[NSRect])->Int? {
        frames.indices.reversed().first{frames[$0].contains(point)}
    }
    static func highlighted(pointerInside:Bool,expanded:Bool,yielded:Bool)->Bool {pointerInside || (expanded && !yielded)}
    // Movement from `from` to `to` heads into the open flyout through the triangle spanned by
    // its near edge, so rows crossed on the way must not replace it (native menu safe zone).
    static func aiming(from:NSPoint,to:NSPoint,flyout:NSRect)->Bool {
        // A slow drift is choosing a row, not travelling into the flyout.
        guard hypot(to.x-from.x,to.y-from.y)>=4 else{return false}
        let edge=flyout.minX>=from.x ? flyout.minX : flyout.maxX
        guard abs(edge-to.x)<abs(edge-from.x)-0.5 else{return false}
        let top=NSPoint(x:edge,y:flyout.maxY+4),bottom=NSPoint(x:edge,y:flyout.minY-4)
        func side(_ a:NSPoint,_ b:NSPoint)->CGFloat {(b.x-a.x)*(to.y-a.y)-(b.y-a.y)*(to.x-a.x)}
        let s1=side(from,top),s2=side(top,bottom),s3=side(bottom,from)
        return (s1>=0 && s2>=0 && s3>=0) || (s1<=0 && s2<=0 && s3<=0)
    }
    static func draw(_ value:String,in rect:NSRect,font:NSFont,color:NSColor,alignment:NSTextAlignment = .left){
        let style=NSMutableParagraphStyle();style.alignment=alignment;style.lineBreakMode = .byWordWrapping
        (value as NSString).draw(with:rect,options:[.usesLineFragmentOrigin,.usesFontLeading],attributes:[.font:font,.foregroundColor:color,.paragraphStyle:style],context:nil)
    }
}

final class Row:NSButton {
    override func acceptsFirstMouse(for event:NSEvent?)->Bool{true}
    let entry:Entry;var pointerAllowed:(()->Bool)?;var invoke:((Entry,Row)->Void)?;var hover:((Entry,Row)->Void)?
    var expanded=false {didSet{needsDisplay=true}}
    // The pointer is on a sibling row, so this row's open flyout no longer owns the highlight.
    var yielded=false {didSet{if yielded != oldValue{needsDisplay=true}}}
    var columnInset:CGFloat=12
    private(set) var configurationClick=false
    private var hovering=false;private var tracking:NSTrackingArea?;private var glyph:NSImage?
    init(_ entry:Entry,frame:NSRect){
        self.entry=entry;super.init(frame:frame);title=entry.label;isBordered=false;setButtonType(.momentaryChange)
        target=self;action=#selector(activate);isEnabled=entry.actionable
        setAccessibilityLabel(entry.label+(entry.value.map{", "+$0} ?? "")+(entry.detail.map{", "+$0} ?? ""))
        if entry.isModule && !entry.children.isEmpty{setAccessibilityHelp("点击切换主页显示；悬停或点击右侧箭头配置连接") }
        if let raw=entry.image,let data=Data(base64Encoded:raw),let img=NSImage(data:data){glyph=img;glyph?.isTemplate=entry.template ?? false}
        else if let symbol=entry.symbol{glyph=NSImage(systemSymbolName:symbol,accessibilityDescription:nil)}
    }
    required init?(coder:NSCoder){fatalError()}
    @objc private func activate(){invoke?(entry,self)}
    override func viewDidChangeEffectiveAppearance(){super.viewDidChangeEffectiveAppearance();needsDisplay=true}
    override func updateTrackingAreas(){super.updateTrackingAreas();if let tracking{removeTrackingArea(tracking)};tracking=NSTrackingArea(rect:bounds,options:[.mouseEnteredAndExited,.activeAlways,.inVisibleRect],owner:self,userInfo:nil);addTrackingArea(tracking!)}
    override func mouseDown(with event:NSEvent){
        guard pointerAllowed?() ?? true else{return}
        configurationClick=MenuLayout.configurationClick(entry,x:convert(event.locationInWindow,from:nil).x,width:bounds.width)
        defer{configurationClick=false}
        super.mouseDown(with:event)
    }
    override func mouseEntered(with event:NSEvent){guard pointerAllowed?() ?? true else{return};hovering=true;needsDisplay=true;hover?(entry,self)}
    override func mouseExited(with event:NSEvent){hovering=false;needsDisplay=true}
    override func draw(_ dirtyRect:NSRect){
        let pointerInside=(pointerAllowed?() ?? true) && (window.map{$0.isVisible && $0.convertToScreen(convert(bounds,to:nil)).contains(NSEvent.mouseLocation)} ?? false)
        if MenuLayout.highlighted(pointerInside:pointerInside,expanded:expanded,yielded:yielded) && isEnabled {NSColor.controlAccentColor.withAlphaComponent(dark(self) ? 0.12 : 0.09).setFill();NSBezierPath(roundedRect:bounds,xRadius:7,yRadius:7).fill()}
        let left=columnInset
        if let checked=entry.checked {
            let box=NSRect(x:13,y:(bounds.height-12)/2,width:12,height:12)
            let radio=entry.radio==true
            let outline=NSBezierPath(roundedRect:box,xRadius:radio ? 6 : 3,yRadius:radio ? 6 : 3)
            if checked {
                NSColor.controlAccentColor.setFill();outline.fill()
                NSColor.white.setStroke();NSColor.white.setFill()
                if radio {NSBezierPath(ovalIn:box.insetBy(dx:4,dy:4)).fill()}
                else {let tick=NSBezierPath();tick.move(to:NSPoint(x:box.minX+2.5,y:box.midY));tick.line(to:NSPoint(x:box.minX+5,y:box.maxY-3));tick.line(to:NSPoint(x:box.maxX-2.5,y:box.minY+3));tick.lineWidth=1.5;tick.lineCapStyle = .round;tick.lineJoinStyle = .round;tick.stroke()}
            } else {NSColor.secondaryLabelColor.withAlphaComponent(0.5).setStroke();outline.lineWidth=1;outline.stroke()}

        } else if let glyph {
            if glyph.isTemplate {
                let image=NSImage(size:NSSize(width:14,height:14));image.lockFocus();glyph.draw(in:NSRect(x:0,y:0,width:14,height:14));(dark(self) ? NSColor(calibratedWhite:0.9,alpha:1) : NSColor(calibratedWhite:0.18,alpha:1)).setFill();NSRect(x:0,y:0,width:14,height:14).fill(using:.sourceIn);image.unlockFocus();image.draw(in:NSRect(x:12,y:(bounds.height-14)/2,width:14,height:14))
            } else {glyph.draw(in:NSRect(x:12,y:(bounds.height-14)/2,width:14,height:14))}
        }
        let trailing=MenuLayout.trailing(entry),font=MenuLayout.labelFont(entry)
        let color:NSColor=entry.action?.first=="stop" ? .systemRed : (entry.actionable || entry.usageLevel != nil || entry.checked==true ? .labelColor : .secondaryLabelColor)
        let labelWidth=MenuLayout.labelWidth(entry,width:bounds.width,left:left)
        let titleHeight=MenuLayout.height(entry.label,font:font,width:labelWidth)
        let detailHeight=entry.detail.map{MenuLayout.height($0,font:MenuLayout.detailFont,width:bounds.width-left-trailing)} ?? 0
        let blockHeight=titleHeight+(entry.detail == nil ? 0 : detailHeight+3)
        let top=(bounds.height-blockHeight)/2
        if let state=entry.statusLight {drawStatusLight(state,centre:NSPoint(x:19,y:top+titleHeight/2),in:self)}
        MenuLayout.draw(entry.label,in:NSRect(x:left,y:top,width:labelWidth,height:titleHeight),font:font,color:color)
        // With a detail line below, the value sits on the title line so the two never overlap.
        let line=entry.detail == nil ? bounds.midY : top+titleHeight/2
        if let value=entry.value {
            let valueWidth=MenuLayout.valueWidth(entry)
            let valueRect=NSRect(x:bounds.width-trailing-valueWidth,y:line-10,width:valueWidth,height:20)
            let textHeight=MenuLayout.height(value,font:MenuLayout.valueFont,width:valueWidth)
            if entry.attention==true {
                NSColor.controlAccentColor.withAlphaComponent(dark(self) ? 0.22 : 0.10).setFill()
                NSBezierPath(roundedRect:valueRect,xRadius:7,yRadius:7).fill()
                MenuLayout.draw(value,in:NSRect(x:valueRect.minX+8,y:line-textHeight/2,width:valueRect.width-16,height:textHeight),font:MenuLayout.valueFont,color:dark(self) ? accent("opencodex",self) : .controlAccentColor,alignment:.center)
            }else if let configured=MenuLayout.configured(value){
                drawStatusLight(configured ? "ok" : "unknown",centre:NSPoint(x:valueRect.minX+3.5,y:line),in:self)
                MenuLayout.draw(value,in:NSRect(x:valueRect.minX+12,y:line-textHeight/2,width:valueRect.width-12,height:textHeight),font:MenuLayout.valueFont,color:.secondaryLabelColor,alignment:.right)
            }else{
                MenuLayout.draw(value,in:NSRect(x:valueRect.minX,y:line-textHeight/2,width:valueRect.width,height:textHeight),font:MenuLayout.valueFont,color:usageColor(entry.usageLevel) ?? (entry.actionable ? .labelColor : .secondaryLabelColor),alignment:.right)
            }
        }
        if let detail=entry.detail {MenuLayout.draw(detail,in:NSRect(x:left,y:top+titleHeight+3,width:bounds.width-left-trailing,height:detailHeight),font:MenuLayout.detailFont,color:.secondaryLabelColor)}

        if !entry.children.isEmpty{let p=NSBezierPath();p.move(to:NSPoint(x:bounds.width-19,y:line-4));p.line(to:NSPoint(x:bounds.width-15,y:line));p.line(to:NSPoint(x:bounds.width-19,y:line+4));p.lineWidth=1.2;p.lineJoinStyle = .round;p.lineCapStyle = .round;NSColor.labelColor.setStroke();p.stroke()}
    }
}
struct Flyout {var panel:Panel;var key:String;weak var row:Row?}
// Presentation is optimistic, but a saved preference revision wins over an older data refresh.
struct PresentationPreferences:Decodable {
    var revision:Int64;var visible:[String];var appearance:String;var allowCollapse:Bool;var expandedProviders:[String]
}
struct ActionReceipt:Decodable {var id:String;var status:String;var preferences:PresentationPreferences?}
struct PanelInteraction {
    enum Submission {case start,queued,ignored}
    var source:Snapshot?;var confirmed:PresentationPreferences?;var active:[String]?;var queued:[[String]]=[]
    static func isPreference(_ action:[String])->Bool {
        guard let name=action.first else{return false}
        return ["set-module","set-appearance","reset-modules","set-collapse","set-expanded"].contains(name)
    }
    // Actions without a dialog keep the panel open so their result appears in place.
    static func keepsPanelOpen(_ action:[String])->Bool {
        ["refresh-view","refresh-source","refresh","refresh-monitor","check-app-updates","set-subscription","set-range","test-api-key","retry-claude-usage"].contains(action.first ?? "") || isPreference(action)
    }
    static func automatic(_ action:[String])->Bool {
        ["refresh-view","refresh-source"].contains(action.first ?? "")
    }
    // SwiftBar's name for a plugin is its file name up to the first dot. SwiftBar 2.1.0/2.1.1
    // match a swiftbar:// link only by that name or the full path, never by the whole file name.
    static func swiftBarName(_ path:String)->String {
        String(URL(fileURLWithPath:path).lastPathComponent.split(separator:".",omittingEmptySubsequences:false).first ?? "")
    }
    static func mayExpire(_ action:[String],claimed:Bool)->Bool {
        // SwiftBar can miss a wake-up. A claimed user action can legitimately wait for a dialog.
        automatic(action) || isPreference(action) || !claimed
    }
    // While an action runs, only the row that started it says so.
    static func runningLabel(_ entry:Entry,active:[String]?)->String? {
        active?.first=="test-api-key" && entry.action==active ? "正在测试…" : nil
    }
    mutating func submit(_ action:[String])->Submission {
        guard !action.isEmpty else{return .ignored}
        guard active != nil else{active=action;return .start}
        guard !Self.automatic(action),queued.count<64 else{return .ignored}
        if !Self.isPreference(action) && (action==active || queued.contains(action)){return .ignored}
        queued.append(action);return .queued
    }
    mutating func finish(_ preferences:PresentationPreferences?) {
        if let preferences,preferences.revision >= (confirmed?.revision ?? -1){confirmed=preferences}
        active=nil
    }
    mutating func next()->[String]? {
        guard active==nil,!queued.isEmpty else{return nil}
        let action=queued.removeFirst();active=action;return action
    }
    func displayed()->Snapshot? {
        guard var value=source else{return nil}
        var authoritative=value.preferences
        if let confirmed,confirmed.revision > (authoritative?.revision ?? -1){authoritative=confirmed}
        if let authoritative{Self.applySettings(authoritative,to:&value)}
        for action in (active.map{[$0]} ?? [])+queued{Self.applyPreference(action,to:&value)}
        return value
    }
    static func applySettings(_ settings:PresentationPreferences,to value:inout Snapshot) {
        value.preferences=settings;value.appearance=settings.appearance
        value.dashboard?.allowCollapse=settings.allowCollapse;value.dashboard?.expandedProviders=settings.expandedProviders
        value.dashboard?.showSummary=settings.visible.contains("summary")
        value.dashboard?.providers.removeAll{!settings.visible.contains($0.id)}
        if let options=value.moduleOptions {
            for option in options where option.action?.first=="set-module" {
                guard let id=option.action?.dropFirst().first else{continue}
                applyPreference(["set-module",id,settings.visible.contains(id) ? "true" : "false"],to:&value)
            }
        }
        syncCollapseOption(settings.allowCollapse,to:&value)
        applyPreference(["set-appearance",settings.appearance],to:&value)
    }
    static func syncCollapseOption(_ enabled:Bool,to value:inout Snapshot){
        value.dashboard?.allowCollapse=enabled
        for i in value.moduleOptions?.indices ?? 0..<0 where value.moduleOptions?[i].action?.first=="set-collapse" {
            value.moduleOptions?[i].checked=enabled;value.moduleOptions?[i].action=["set-collapse",enabled ? "false" : "true"]
        }
    }
    static func applyPreference(_ action:[String],to value:inout Snapshot){
        guard let kind=action.first else{return}
        if kind=="set-module",action.count==3 {
            for i in value.moduleOptions?.indices ?? 0..<0 where value.moduleOptions?[i].action?.prefix(2).elementsEqual(action.prefix(2)) == true {
                let enabled=action[2]=="true";value.moduleOptions?[i].checked=enabled
                value.moduleOptions?[i].action=["set-module",action[1],enabled ? "false" : "true"]
            }
        }
        if kind=="set-collapse",action.count==2 {
            let enabled=action[1]=="true"
            // Turning folding on starts with every card folded.
            if enabled{value.dashboard?.expandedProviders=[]}
            syncCollapseOption(enabled,to:&value)
        }
        if kind=="set-expanded",action.count==3 {
            var expanded=Set(value.dashboard?.expandedProviders ?? [])
            if action[2]=="true"{expanded.insert(action[1])}else{expanded.remove(action[1])}
            value.dashboard?.expandedProviders=expanded.sorted()
        }
        if kind=="set-appearance",action.count==2 {
            value.appearance=action[1]
            func update(_ entries:inout [Entry]){
                for i in entries.indices {
                    if entries[i].action?.first=="set-appearance" {entries[i].checked=entries[i].action?.last==action[1]}
                    update(&entries[i].children)
                }
            }
            if var entries=value.moduleOptions{update(&entries);value.moduleOptions=entries}
        }
    }

}
// Atomic JSON publication replaces files, so observe the containing directory.
// A closed panel has no polling timer and no global pointer monitors.
final class SnapshotObserver {
    private var source:DispatchSourceFileSystemObject?
    private var debounce:DispatchWorkItem?
    var active:Bool {source != nil}
    func start(directory:String,changed:@escaping ()->Void) {
        stop()
        let fd=open(directory,O_EVTONLY)
        guard fd>=0 else{return}
        let watcher=DispatchSource.makeFileSystemObjectSource(fileDescriptor:fd,eventMask:[.write,.delete,.rename,.revoke],queue:.main)
        watcher.setCancelHandler{close(fd)}
        watcher.setEventHandler{[weak self] in
            guard let self else{return}
            if let events=self.source?.data,!events.intersection([.delete,.rename,.revoke]).isEmpty {self.stop();changed();return}
            self.debounce?.cancel()
            let task=DispatchWorkItem(block:changed);self.debounce=task
            DispatchQueue.main.asyncAfter(deadline:.now()+0.08,execute:task)
        }
        source=watcher;watcher.resume()
    }
    func stop(){debounce?.cancel();debounce=nil;source?.cancel();source=nil}
    deinit{stop()}
}

// Submenus switch a fixed delay after the pointer enters a row, as native menus do;
// further movement inside the same row keeps the countdown instead of restarting it.
final class HoverIntent {
    var delay:TimeInterval=0.08
    private(set) var target:String?
    private var task:DispatchWorkItem?
    func schedule(_ key:String,after wait:TimeInterval?=nil,_ action:@escaping ()->Void){
        if target==key{return}
        cancel();target=key
        let item=DispatchWorkItem{[weak self] in self?.target=nil;self?.task=nil;action()}
        task=item;DispatchQueue.main.asyncAfter(deadline:.now()+(wait ?? delay),execute:item)
    }
    func cancel(){task?.cancel();task=nil;target=nil}
}

final class App:NSObject,NSApplicationDelegate,NSMenuDelegate,NSSharingServicePickerDelegate {
    private let panel=Panel(contentRect:.zero,styleMask:[.borderless,.nonactivatingPanel],backing:.buffered,defer:false)
    private var rangePicker:NSPopUpButton?;private var anchoredMenuOpen=false;private var toast:(text:String,until:Date,ok:Bool)?;private var toastView:NSView?;private weak var shareButton:NSButton?;private weak var detectButton:NSButton?;private var hintTask:DispatchWorkItem?;private var hintView:NSView?;private var userApp:NSRunningApplication?;private var firstActivation=true;private var managerOnly=false
    private var snapshot:Snapshot?;private var snapshotPath="";private var pluginPath="";private var modified:Date?
    private var anchor:NSPoint = .zero;private var anchorRect:NSRect = .zero;private var scrollOffset:CGFloat=0
    private var fallbackTimer:Timer?;private let observer=SnapshotObserver();private var actionTimeout:DispatchWorkItem?;private var reloadTask:DispatchWorkItem?;private var monitors:[Any]=[];private var pendingAction:String?
    private var interaction=PanelInteraction()
    private var sourceChecks:[String:Date]=[:]
    private var flyouts:[Flyout]=[];private var rows:[[String:Row]]=[];private let hoverIntent=HoverIntent();private var closeTask:DispatchWorkItem?;private var closeLevel=0
    private var trail:[(point:NSPoint,time:TimeInterval)]=[]
    private func setup(_ window:Panel){window.backgroundColor = .clear;window.isOpaque=false;window.hasShadow=true;window.level = .popUpMenu;window.hidesOnDeactivate=false;window.isReleasedWhenClosed=false;window.acceptsMouseMovedEvents=true;window.collectionBehavior=[.canJoinAllSpaces,.fullScreenAuxiliary,.transient,.ignoresCycle];window.escape={[weak self] in self?.hide()}}
    func applicationDidFinishLaunching(_ notification:Notification){
        let args=CommandLine.arguments
        guard let si=args.firstIndex(of:"--snapshot"),si+1<args.count,let pi=args.firstIndex(of:"--plugin"),pi+1<args.count else{NSApp.terminate(nil);return}
        snapshotPath=args[si+1];pluginPath=args[pi+1];setup(panel);panel.setAccessibilityLabel("AI 用量面板")
        observeSnapshot()
        NSWorkspace.shared.notificationCenter.addObserver(self,selector:#selector(accessibilityChanged),name:NSWorkspace.accessibilityDisplayOptionsDidChangeNotification,object:nil)
        if let front=NSWorkspace.shared.frontmostApplication,front != NSRunningApplication.current{userApp=front}
        NSWorkspace.shared.notificationCenter.addObserver(forName:NSWorkspace.didActivateApplicationNotification,object:nil,queue:.main){[weak self] note in
            if let app=note.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication,app != NSRunningApplication.current{self?.userApp=app}
        }
        NotificationCenter.default.addObserver(self,selector:#selector(rangeTrackingBegan(_:)),name:NSMenu.didBeginTrackingNotification,object:nil)
        NotificationCenter.default.addObserver(self,selector:#selector(rangeTrackingEnded(_:)),name:NSMenu.didEndTrackingNotification,object:nil)
        show()
    }
    private func observeSnapshot(){
        guard !observer.active else{return}
        observer.start(directory:URL(fileURLWithPath:snapshotPath).deletingLastPathComponent().path){[weak self] in
            guard let self else{return};self.checkAction();self.updateFallback();if self.panel.isVisible{self.reload()}
        }
        updateFallback()
    }
    private func installPointerMonitors(){
        guard monitors.isEmpty else{return}
        if let monitor=NSEvent.addGlobalMonitorForEvents(matching:[.leftMouseDown,.rightMouseDown],handler:{[weak self] event in
            // The system share sheet draws in another process, so its clicks arrive here as outside clicks.
            guard let self,!self.anchoredMenuOpen else{return}
            // SwiftBar routes right-click and Option-click to its native menu.
            // Only an ordinary left-click on the star retains the panel toggle.
            if event.type == .rightMouseDown || event.modifierFlags.contains(.option) || !self.anchorRect.contains(NSEvent.mouseLocation){self.hide(returningFocus:false)}
        }){monitors.append(monitor)}
        if let monitor=NSEvent.addGlobalMonitorForEvents(matching:.mouseMoved,handler:{[weak self] _ in self?.checkPointer()}){monitors.append(monitor)}
        if let monitor=NSEvent.addLocalMonitorForEvents(matching:.mouseMoved,handler:{[weak self] event in self?.checkPointer();return event}){monitors.append(monitor)}
        if let monitor=NSEvent.addLocalMonitorForEvents(matching:.rightMouseDown,handler:{[weak self] event in
            guard let self,let window=event.window,(window === self.panel || self.flyouts.contains(where:{$0.panel === window})),let view=window.contentView else{return event}
            self.showModuleMenu(event,in:view);return nil
        }){monitors.append(monitor)}
    }
    private func removePointerMonitors(){for monitor in monitors{NSEvent.removeMonitor(monitor)};monitors=[]}
    private func publishVisibility(_ visible:Bool){
        let url=URL(fileURLWithPath:snapshotPath).deletingLastPathComponent().appendingPathComponent("panel-state.json")
        if let data=try? JSONSerialization.data(withJSONObject:["schema":1,"visible":visible,"pid":ProcessInfo.processInfo.processIdentifier]) {
            try? data.write(to:url,options:.atomic);try? FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:url.path)
        }
        updateFallback()
    }
    private func updateFallback(){
        fallbackTimer?.invalidate();fallbackTimer=nil
        guard !observer.active,(panel.isVisible || pendingAction != nil) else{return}
        fallbackTimer=Timer.scheduledTimer(withTimeInterval:1,repeats:true){[weak self] _ in
            guard let self else{return};self.observeSnapshot();self.checkAction();if self.panel.isVisible{self.reload()}
        };fallbackTimer?.tolerance=0.25
    }
    func applicationWillTerminate(_ notification:Notification){publishVisibility(false);observer.stop();removePointerMonitors();fallbackTimer?.invalidate()}
    @objc private func accessibilityChanged(){if panel.isVisible{reload(force:true)}}
    private func showModuleMenu(_ event:NSEvent,in view:NSView){
        rangePicker?.menu?.cancelTracking();anchoredMenuOpen=false;hoverIntent.cancel();closeTask?.cancel();closeFlyouts(from:0)
        managerOnly=true;scrollOffset=0;layout()
    }
    @objc private func finishModules(){hide()}
    private func requestedMode()->Bool? {
        let request=URL(fileURLWithPath:snapshotPath).deletingLastPathComponent().appendingPathComponent("panel-open.json")
        guard let data=try? Data(contentsOf:request),let value=try? JSONSerialization.jsonObject(with:data) as? [String:Any],let created=value["createdAt"] as? Double,Date().timeIntervalSince1970-created<30 else{return nil}
        try? FileManager.default.removeItem(at:request)
        return value["mode"] as? String=="modules"
    }
    // Opening the app (the star) activates it. On launch that comes before the panel has been built, so
    // the first activation is left alone; later, if it lands after the panel closed, focus goes straight
    // back. A short wait tells a reopen that shows the panel from one that hides it.
    func applicationDidBecomeActive(_ notification:Notification){
        if firstActivation{firstActivation=false;return}
        DispatchQueue.main.asyncAfter(deadline:.now()+0.6){[weak self] in guard let self,!self.panel.isVisible else{return};self.returnFocus()}
    }
    func applicationShouldHandleReopen(_ sender:NSApplication,hasVisibleWindows flag:Bool)->Bool{let requested=requestedMode();if panel.isVisible && (requested == nil || requested==managerOnly){hide()}else{if let requested{managerOnly=requested};show()};return false}
    private func nativeMenuVisible()->Bool {
        let windows=CGWindowListCopyWindowInfo([.optionOnScreenOnly,.excludeDesktopElements],kCGNullWindowID) as? [[String:Any]] ?? []
        return windows.contains {row in
            guard row[kCGWindowOwnerName as String] as? String == "SwiftBar",
                  let layer=row[kCGWindowLayer as String] as? Int,layer==NSWindow.Level.popUpMenu.rawValue,
                  let bounds=row[kCGWindowBounds as String] as? [String:Double] else{return false}
            return (bounds["Width"] ?? 0)>80 && (bounds["Height"] ?? 0)>30
        }
    }
    private func show(){if let requested=requestedMode(){managerOnly=requested};guard !nativeMenuVisible() else{hide();return};scrollOffset=0;anchor=NSEvent.mouseLocation;let screen=NSScreen.screens.first(where:{$0.frame.contains(anchor)}) ?? NSScreen.main!;anchorRect=NSRect(x:anchor.x-24,y:screen.visibleFrame.maxY,width:48,height:screen.frame.maxY-screen.visibleFrame.maxY+8);reload(force:true);panel.makeKeyAndOrderFront(nil);activateForPanel();observeSnapshot();installPointerMonitors();publishVisibility(true);if !managerOnly{performAction(["refresh-view"])}}
    // Glass and controls draw their inactive, greyer style until the app is active, so the panel used to
    // change colour on the first click. Opening the panel activates it; closing hands focus back.
    private func activateForPanel(){
        guard !NSApp.isActive else{return}
        if #available(macOS 14.0,*){NSApp.activate()}else{NSApp.activate(ignoringOtherApps:true)}
    }
    // Deactivating alone leaves no app focused, so hand focus to the app the user was in. Not when the
    // panel closed because they clicked another app or opened a web page: that app is coming forward.
    private func returnFocus(){
        guard NSApp.isActive,let app=userApp,!app.isTerminated else{return}
        if #available(macOS 14.0,*){NSApp.yieldActivation(to:app);app.activate()}else{app.activate(options:[])}
    }
    private func hide(returningFocus:Bool=true){if returningFocus{returnFocus()};hintTask?.cancel();hintView?.removeFromSuperview();hintView=nil;rangePicker?.menu?.cancelTracking();hoverIntent.cancel();closeTask?.cancel();closeFlyouts(from:0);panel.orderOut(nil);removePointerMonitors();reloadTask?.cancel();publishVisibility(false)}
    private func updatePresentation(){snapshot=interaction.displayed();layout()}
    private func reload(force:Bool=false){
        // Do not replace a button while the user is pressing it.
        guard !anchoredMenuOpen else{return}
        let moving = !flyouts.isEmpty && ProcessInfo.processInfo.systemUptime-(trail.last?.time ?? 0)<0.3
        if NSEvent.pressedMouseButtons & 1 != 0 || moving {
            reloadTask?.cancel();let task=DispatchWorkItem{[weak self] in self?.reload(force:force)};reloadTask=task
            DispatchQueue.main.asyncAfter(deadline:.now()+0.08,execute:task);return
        }
        let date=(try? FileManager.default.attributesOfItem(atPath:snapshotPath)[.modificationDate]) as? Date
        guard force || date != modified else{return}
        guard let data=try? Data(contentsOf:URL(fileURLWithPath:snapshotPath)),let value=try? JSONDecoder().decode(Snapshot.self,from:data) else{return}
        modified=date;interaction.source=value;updatePresentation()
    }
    private func fillRows(_ entries:[Entry],in content:NSView,y start:CGFloat,width:CGFloat,depth:Int)->CGFloat {
        while rows.count<=depth{rows.append([:])};rows[depth]=[:];var y=start
        for original in entries {var entry=original;if let label=PanelInteraction.runningLabel(entry,active:interaction.active){entry.label=label}
            let left=MenuLayout.column(entries);entry=MenuLayout.fitted(entry,width:width,left:left);let h=MenuLayout.rowHeight(entry,width:width,left:left)
            if entry.separator==true {content.addSubview(depth==0 ? Divider(frame:NSRect(x:8,y:y+5,width:width-16,height:2)) : {let line=NSBox(frame:NSRect(x:8,y:y+5,width:width-16,height:1));line.boxType = .separator;return line}())}
            else {let row=Row(entry,frame:NSRect(x:0,y:y,width:width,height:h));row.columnInset=entry.section==true ? 12 : left;row.pointerAllowed={[weak self,weak row] in guard let self,let row else{return false};return self.pointerHits(row)};row.invoke={[weak self] e,r in self?.activate(e,row:r,depth:depth)};row.hover={[weak self] e,r in self?.hover(e,row:r,depth:depth)};content.addSubview(row);rows[depth][entry.key]=row}
            y+=h
        };return y
    }
    private func layout(){
        guard !anchoredMenuOpen,let snapshot else{return};if panel.isVisible,let old=(panel.contentView as? Surface)?.content.subviews.first as? NSScrollView {scrollOffset=old.contentView.bounds.origin.y};let opened=flyouts.map{$0.key};closeFlyouts(from:0);rows=[]
        let screen=NSScreen.screens.first(where:{$0.frame.contains(anchor)}) ?? NSScreen.main!,width:CGFloat=managerOnly ? 280 : 360,inner=width-16
        let entries=managerOnly ? snapshot.moduleOptions ?? [] : snapshot.entries
        let top:CGFloat=managerOnly ? 49 : (snapshot.dashboard.map{DashboardView.height($0)+8} ?? 36)
        let body=entries.reduce(CGFloat(0)){$0+MenuLayout.rowHeight($1,width:inner,left:MenuLayout.column(entries))},contentHeight=top+body
        let height=min(contentHeight+16,screen.visibleFrame.height-16)
        let surface=Surface(frame:NSRect(x:0,y:0,width:width,height:height),glass:self.snapshot?.appearance != "solid")
        let scroll=ContentScrollView(frame:NSRect(x:8,y:8,width:inner,height:height-16));scroll.drawsBackground=false;scroll.hasVerticalScroller=contentHeight>scroll.frame.height;scroll.scrollerStyle = .overlay
        let content=FlippedView(frame:NSRect(x:0,y:0,width:inner,height:contentHeight))
        if managerOnly {
            let heading=NSTextField(labelWithString:"主页模块");heading.font = .systemFont(ofSize:14,weight:.semibold);heading.frame=NSRect(x:12,y:7,width:inner-85,height:20);content.addSubview(heading)
            let count=entries.filter{$0.action?.first=="set-module" && $0.checked==true}.count
            let hint=NSTextField(labelWithString:"已启用 \(count) 项");hint.font = .systemFont(ofSize:10);hint.textColor = .secondaryLabelColor;hint.frame=NSRect(x:12,y:29,width:inner-24,height:15);content.addSubview(hint)
            let done=NSButton(title:"完成",target:self,action:#selector(finishModules));done.bezelStyle = .rounded;done.controlSize = .small;done.frame=NSRect(x:inner-65,y:8,width:53,height:25);content.addSubview(done)
        } else if let dashboard=snapshot.dashboard{let view=DashboardView(dashboard,width:inner);view.toggleProvider={[weak self] id,expanded in self?.performAction(["set-expanded",id,expanded ? "true" : "false"])};content.addSubview(view)}else{let label=NSTextField(labelWithString:"AI 用量面板");label.frame=NSRect(x:12,y:10,width:300,height:20);content.addSubview(label)}
        content.addSubview(Divider(frame:NSRect(x:8,y:top-6,width:inner-16,height:2)))
        _=fillRows(entries,in:content,y:top,width:inner,depth:0)
        if !managerOnly,let dashboard=snapshot.dashboard,let choices=snapshot.rangeOptions {
            let picker=NSPopUpButton(frame:NSRect(x:inner-113,y:7.5,width:101,height:25),pullsDown:true)
            picker.controlSize = .small;picker.font = .systemFont(ofSize:11);picker.bezelStyle = .rounded
            picker.preferredEdge = .maxY;picker.setAccessibilityLabel("统计范围 · "+dashboard.rangeLabel)
            let menu=NSMenu(title:"统计范围");menu.autoenablesItems=false;menu.delegate=self
            let title=NSMenuItem(title:dashboard.rangeLabel,action:nil,keyEquivalent:"");menu.addItem(title)
            for choice in choices {
                let option=NSMenuItem(title:choice.label,action:#selector(chooseRange(_:)),keyEquivalent:"")
                option.target=self;option.representedObject=choice.action;option.state=choice.checked==true ? .on : .off
                option.isEnabled = true;menu.addItem(option)
            }
            picker.menu=menu;menu.delegate=self;rangePicker=picker;content.addSubview(picker)
            // Only the snapshot marks a range. Left to itself, the button also checks the clicked option,
            // which showed two checks while the previous range stays on screen.
            (picker.cell as? NSPopUpButtonCell)?.altersStateOfSelectedItem=false
            // Refresh leaves the button and current data in place; duplicate actions are coalesced.
            let refresh=HintButton(frame:NSRect(x:inner-145,y:7.5,width:28,height:25))
            refresh.bezelStyle = .rounded;refresh.controlSize = .small;refresh.title=""
            refresh.image=NSImage(systemSymbolName:"arrow.clockwise",accessibilityDescription:"刷新数据")?.withSymbolConfiguration(.init(pointSize:11,weight:.semibold));refresh.imagePosition = .imageOnly
            refresh.contentTintColor = .labelColor;refresh.hint="刷新数据";refresh.hovered={[weak self] in self?.hoverHint($0,$1)};refresh.setAccessibilityLabel("刷新数据")
            refresh.target=self;refresh.action=#selector(refreshData)
            content.addSubview(refresh)
            let share=HintButton(frame:NSRect(x:inner-177,y:7.5,width:28,height:25))
            share.bezelStyle = .rounded;share.controlSize = .small;share.title=""
            share.image=NSImage(systemSymbolName:"square.and.arrow.up",accessibilityDescription:"分享用量图")?.withSymbolConfiguration(.init(pointSize:11,weight:.semibold));share.imagePosition = .imageOnly
            share.contentTintColor = .labelColor;share.hint="分享用量图";share.hovered={[weak self] in self?.hoverHint($0,$1)};share.setAccessibilityLabel("分享用量图")
            share.target=self;share.action=#selector(shareDashboard(_:));content.addSubview(share);shareButton=share
            // Auto-detect is a rarely used setup action, so it is a plain icon like its neighbours; the
            // hover hint and accessibility label name it, and it always asks before changing anything.
            let detect=HintButton(frame:NSRect(x:inner-209,y:7.5,width:28,height:25))
            detect.bezelStyle = .rounded;detect.controlSize = .small;detect.title=""
            detect.image=NSImage(systemSymbolName:"sparkle.magnifyingglass",accessibilityDescription:"自动检测")?.withSymbolConfiguration(.init(pointSize:11,weight:.semibold));detect.imagePosition = .imageOnly
            detect.contentTintColor = .labelColor;detect.setAccessibilityLabel("自动检测")
            detect.hint="自动检测";detect.hovered={[weak self] in self?.hoverHint($0,$1)}
            detect.target=self;detect.action=#selector(detectModules);content.addSubview(detect);detectButton=detect
        }
        scroll.documentView=content;scroll.configureScrolling();scroll.contentView.scroll(to:NSPoint(x:0,y:min(scrollOffset,max(0,contentHeight-scroll.frame.height))));surface.content.addSubview(scroll);panel.contentView=surface
        let x=min(max(anchor.x-22,screen.visibleFrame.minX+4),screen.visibleFrame.maxX-width-4)
        panel.setFrame(NSRect(x:x,y:screen.visibleFrame.maxY-height-4,width:width,height:height),display:true);placeToast()
        for (depth,key) in opened.enumerated(){guard rows.indices.contains(depth),let row=rows[depth][key] else{break};openFlyout(row.entry,row:row,depth:depth,checkSource:false)}
        DispatchQueue.main.async{[weak self] in self?.checkPointer()}
    }
    private func rowRect(_ row:Row)->NSRect{guard let window=row.window else{return .zero};return window.convertToScreen(row.convert(row.bounds,to:nil))}
    private func topPanel(at point:NSPoint)->NSWindow? {
        let windows=([panel]+flyouts.map{$0.panel}).filter{$0.isVisible}
        guard let index=MenuLayout.topmostIndex(at:point,frames:windows.map{$0.frame}) else{return nil}
        return windows[index]
    }
    private func pointerHits(_ row:Row)->Bool {topPanel(at:NSEvent.mouseLocation) === row.window && rowRect(row).contains(NSEvent.mouseLocation)}
    private func allowsConfigurationHover(_ row:Row)->Bool {
        guard let window=row.window else{return false}
        return MenuLayout.configurationHover(row.entry,x:row.convert(window.convertPoint(fromScreen:NSEvent.mouseLocation),from:nil).x)
    }
    private func hover(_ entry:Entry,row:Row,depth:Int){
        guard !anchoredMenuOpen,pointerHits(row) else{return}
        closeTask?.cancel()
        // A left-opening flyout is reached through the checkbox column.
        // Suppress new hover requests here, but retain the already open panel.
        guard allowsConfigurationHover(row) else{hoverIntent.cancel();return}
        hoverIntent.schedule(String(depth)+"\u{1f}"+entry.key){[weak self,weak row] in self?.settleHover(entry,row:row,depth:depth,postponed:0)}
    }
    private func settleHover(_ entry:Entry,row:Row?,depth:Int,postponed:Int){
        guard let row,pointerHits(row),allowsConfigurationHover(row) else{return}
        // Still travelling into the open flyout: look again shortly, but never hold the old row for long.
        if postponed<2,aimingAtFlyout(depth) {
            hoverIntent.schedule(String(depth)+"\u{1f}"+entry.key,after:0.06){[weak self,weak row] in self?.settleHover(entry,row:row,depth:depth,postponed:postponed+1)}
            return
        }
        if entry.children.isEmpty{closeFlyouts(from:depth)}else{openFlyout(entry,row:row,depth:depth)}
    }
    private func recordPointer(_ point:NSPoint){
        let now=ProcessInfo.processInfo.systemUptime
        if trail.last?.point != point{trail.append((point,now))}
        trail.removeAll{now-$0.time>0.25}
    }
    private func aimingAtFlyout(_ depth:Int)->Bool {
        guard flyouts.indices.contains(depth),let current=trail.last else{return false}
        let now=ProcessInfo.processInfo.systemUptime
        // A resting pointer is choosing the row under it, not heading anywhere.
        guard now-current.time<0.06,let start=trail.first(where:{now-$0.time<=0.12}) else{return false}
        return MenuLayout.aiming(from:start.point,to:current.point,flyout:flyouts[depth].panel.frame)
    }
    private func openFlyout(_ entry:Entry,row:Row,depth:Int,checkSource:Bool=true){
        guard !entry.children.isEmpty,let parent=row.window else{return}
        if flyouts.indices.contains(depth),flyouts[depth].key==entry.key{return}
        closeFlyouts(from:depth);row.expanded=true;row.yielded=false
        let window=Panel(contentRect:.zero,styleMask:[.borderless,.nonactivatingPanel],backing:.buffered,defer:false);setup(window);window.setAccessibilityLabel(entry.label)
        let screen=parent.screen ?? NSScreen.main!,width=MenuLayout.flyoutWidth(entry),body=entry.children.reduce(CGFloat(0)){$0+MenuLayout.rowHeight($1,width:MenuLayout.flyoutWidth(entry)-16,left:MenuLayout.column(entry.children))}
        let height=min(body+40,screen.visibleFrame.height-16),surface=Surface(frame:NSRect(x:0,y:0,width:width,height:height),glass:self.snapshot?.appearance != "solid")
        let heading=NSTextField(labelWithString:entry.label);heading.font = .systemFont(ofSize:11,weight:.medium);heading.textColor = .secondaryLabelColor;let headingLeft=MenuLayout.column(entry.children)+8;heading.frame=NSRect(x:headingLeft,y:10,width:width-headingLeft-20,height:18);surface.content.addSubview(heading)
        let scroll=ContentScrollView(frame:NSRect(x:8,y:32,width:width-16,height:height-40));scroll.drawsBackground=false;scroll.hasVerticalScroller=body>scroll.frame.height;scroll.scrollerStyle = .overlay
        let content=FlippedView(frame:NSRect(x:0,y:0,width:width-16,height:body));_=fillRows(entry.children,in:content,y:0,width:width-16,depth:depth+1);scroll.documentView=content;scroll.configureScrolling();surface.content.addSubview(scroll);window.contentView=surface
        let ancestor=depth>0 ? (depth==1 ? panel.frame : flyouts[depth-2].panel.frame) : nil
        let x=MenuLayout.flyoutX(parent:parent.frame,ancestor:ancestor,width:width,screen:screen.visibleFrame)
        let rect=rowRect(row),y=max(screen.visibleFrame.minY+4,min(rect.maxY+6-height,screen.visibleFrame.maxY-height-4))
        window.setFrame(NSRect(x:x,y:y,width:width,height:height),display:true);parent.addChildWindow(window,ordered:.above);window.orderFront(nil);flyouts.append(Flyout(panel:window,key:entry.key,row:row))
        if checkSource,let action=entry.onOpenAction,action.count==2 {
            let key=action[1],now=Date()
            if now.timeIntervalSince(sourceChecks[key] ?? .distantPast)>=5 {
                sourceChecks[key]=now
                DispatchQueue.main.async{[weak self] in self?.performAction(action)}
            }
        }
    }
    private func closeFlyouts(from depth:Int){
        guard depth<flyouts.count else{return}
        for flyout in flyouts[depth...].reversed(){flyout.row?.expanded=false;flyout.row?.yielded=false;flyout.panel.parent?.removeChildWindow(flyout.panel);flyout.panel.orderOut(nil)}
        flyouts.removeSubrange(depth...);if rows.count>depth+1{rows.removeSubrange((depth+1)...)}
    }
    private func checkPointer(){
        guard panel.isVisible,!anchoredMenuOpen else{return}
        let p=NSEvent.mouseLocation,top=topPanel(at:NSEvent.mouseLocation);recordPointer(p)
        let hit=rows.indices.reversed().lazy.compactMap{depth in self.rows[depth].values.first(where:{$0.window === top && self.rowRect($0).contains(p)}).map{(depth:depth,row:$0)}}.first
        // The highlight follows the pointer at once, unless it is crossing rows on its way into the open flyout.
        for (depth,flyout) in flyouts.enumerated(){flyout.row?.yielded=hit.map{$0.row !== flyout.row && $0.row.window === flyout.row?.window && !aimingAtFlyout(depth)} ?? false}
        for group in rows{for row in group.values{row.needsDisplay=true}}
        if case let (depth,row)? = hit {
            closeTask?.cancel()
            if !allowsConfigurationHover(row){hoverIntent.cancel();return}
            if row.entry.children.isEmpty {
                if flyouts.count>depth{hover(row.entry,row:row,depth:depth)}
            }else if !flyouts.indices.contains(depth) || flyouts[depth].key != row.entry.key {
                hover(row.entry,row:row,depth:depth)
            }
            return
        }
        guard !flyouts.isEmpty else{return}
        // Inside a panel but off its rows (Summary, gaps, a flyout heading): flyouts beyond that panel
        // close shortly, unless the pointer is heading into them. Outside every panel, all of them close.
        guard let top else{scheduleClose(from:0,after:0.15);return}
        let level=top === panel ? 0 : (flyouts.firstIndex{$0.panel === top}.map{$0+1} ?? 0)
        if flyouts.count<=level || aimingAtFlyout(level){closeTask?.cancel();return}
        scheduleClose(from:level,after:0.12)
    }
    private func scheduleClose(from level:Int,after delay:TimeInterval){
        if let closeTask,!closeTask.isCancelled,closeLevel==level{return}
        closeTask?.cancel();closeLevel=level
        let task=DispatchWorkItem{[weak self] in self?.closeFlyouts(from:level);self?.closeTask=nil}
        closeTask=task;DispatchQueue.main.asyncAfter(deadline:.now()+delay,execute:task)
    }
    private func checkAction(){
        guard let id=pendingAction else{return}
        let receipt=URL(fileURLWithPath:snapshotPath).deletingLastPathComponent().appendingPathComponent("panel-action-receipt.json")
        guard let data=try? Data(contentsOf:receipt),let value=try? JSONDecoder().decode(ActionReceipt.self,from:data),value.id==id else{return}
        finishAction(id,preferences:value.preferences,failed:value.status != "done")
    }
    private func finishAction(_ id:String,preferences:PresentationPreferences?=nil,failed:Bool=false){
        guard pendingAction==id else{return}
        let report=failed && !PanelInteraction.automatic(interaction.active ?? [])
        actionTimeout?.cancel();pendingAction=nil;interaction.finish(preferences);updateFallback()
        if panel.isVisible{reload(force:true)}
        if report {showActionFailure("SwiftBar 未完成这次操作，请重试。")}
        if let next=interaction.next(){dispatchAction(next)}
    }
    private func showActionFailure(_ detail:String){
        let alert=NSAlert();alert.messageText="操作未完成";alert.informativeText=detail;alert.addButton(withTitle:"好");alert.runModal()
    }
    private func activate(_ entry:Entry,row:Row,depth:Int){
        if entry.isModule,!row.configurationClick,let action=entry.action{performAction(action);return}
        if !entry.children.isEmpty{openFlyout(entry,row:row,depth:depth);return}
        // Desktop app update rows link to the installed .app, which opens the app to update itself.
        if let href=entry.href,let url=URL(string:href),url.isFileURL,url.pathExtension=="app"{
            hide(returningFocus:false)
            NSWorkspace.shared.openApplication(at:url,configuration:NSWorkspace.OpenConfiguration()){[weak self] _,error in
                if error != nil{DispatchQueue.main.async{self?.showActionFailure("无法打开应用。请确认应用已安装后重试。")}}
            };return
        }
        if let href=entry.href,let url=URL(string:href),["http","https","swiftbar"].contains(url.scheme ?? ""){
            // Web pages come to the front; SwiftBar commands run without taking focus from the user's app.
            let config=NSWorkspace.OpenConfiguration();config.activates=url.scheme != "swiftbar"
            hide(returningFocus:!config.activates)
            NSWorkspace.shared.open(url,configuration:config){[weak self] _,error in
                if error != nil{DispatchQueue.main.async{self?.showActionFailure("系统无法打开此链接，请重试。")}}
            };return
        }
        if let action=entry.action{performAction(action)}
    }
    @objc private func rangeTrackingBegan(_ notification:Notification){if let menu=notification.object as? NSMenu,menu === rangePicker?.menu{menuWillOpen(menu)}}
    @objc private func rangeTrackingEnded(_ notification:Notification){if let menu=notification.object as? NSMenu,menu === rangePicker?.menu{menuDidClose(menu)}}
    func menuWillOpen(_ menu:NSMenu){anchoredMenuOpen=true;hideHint();hoverIntent.cancel();closeTask?.cancel();closeFlyouts(from:0)}
    func menuDidClose(_ menu:NSMenu){anchoredMenuOpen=false;DispatchQueue.main.async{[weak self] in self?.reload(force:true)}}
    // A short label under a header button, 350 ms after the pointer settles on it.
    private func hideHint(){hintTask?.cancel();hintTask=nil;hintView?.removeFromSuperview();hintView=nil}
    private func hoverHint(_ button:HintButton,_ inside:Bool){
        hideHint()
        guard inside,!button.hint.isEmpty,!anchoredMenuOpen else{return}
        let task=DispatchWorkItem{[weak self,weak button] in
            guard let self,!self.anchoredMenuOpen,let button,let surface=button.window?.contentView as? Surface else{return}
            // The bubble is sized from the drawn text and the label spans it centred, so both sides get the same space.
            let label=NSTextField(labelWithString:button.hint);label.font = .systemFont(ofSize:11);label.textColor = .labelColor;label.alignment = .center
            let text=(button.hint as NSString).size(withAttributes:[.font:label.font!]),height=ceil(label.intrinsicContentSize.height)
            let anchor=surface.convert(button.bounds,from:button),width=ceil(text.width)+24
            let bubble=FlippedView(frame:NSRect(x:min(max(8,anchor.midX-width/2),surface.bounds.width-width-8),y:anchor.maxY+4,width:width,height:height+8))
            bubble.wantsLayer=true;bubble.layer?.cornerRadius=6;bubble.layer?.borderWidth=0.5
            surface.effectiveAppearance.performAsCurrentDrawingAppearance{bubble.layer?.backgroundColor=background(surface).cgColor;bubble.layer?.borderColor=NSColor.separatorColor.cgColor}
            label.frame=NSRect(x:0,y:4,width:width,height:height);bubble.addSubview(label)
            surface.addSubview(bubble);self.hintView=bubble
        }
        hintTask=task;DispatchQueue.main.asyncAfter(deadline:.now()+0.35,execute:task)
    }
    @objc private func refreshData(){performAction(["refresh"])}
    @objc private func detectModules(){performAction(["detect-modules"])}
    // The picture covers Summary and the Harness cards only; subscriptions, balances and keys stay private.
    private func sharePicture(_ style:ShareCard.Style)->Data? {
        guard let dashboard=snapshot?.dashboard else{return nil}
        let card=ShareCard.make(style,dashboard,project:snapshot?.project ?? Project(name:"Token Monitor",url:"https://github.com/timecatbit/swiftbar-token-monitor"))
        card.appearance=panel.effectiveAppearance
        return card.png()
    }
    // Copy lives in our own menu: the system share sheet runs in another process, and its clicks
    // used to look like clicks outside the panel, closing it before the copy ran.
    // Each style has its own copy and share, so one click makes the picture people picked.
    @objc private func shareDashboard(_ sender:NSButton){
        let menu=NSMenu();menu.autoenablesItems=false;menu.delegate=self
        for style in ShareCard.Style.allCases {
            if style != ShareCard.Style.allCases.first {menu.addItem(.separator())}
            menu.addItem(Self.menuHeader(style.title))
            for (title,symbol,action) in [("复制图片","doc.on.doc",#selector(copySharePicture(_:))),("分享…","square.and.arrow.up",#selector(openSharePicker(_:)))]{
                let item=NSMenuItem(title:title,action:action,keyEquivalent:"");item.target=self;item.representedObject=sender;item.tag=style.rawValue
                item.image=NSImage(systemSymbolName:symbol,accessibilityDescription:nil);menu.addItem(item)
            }
        }
        menu.popUp(positioning:nil,at:NSPoint(x:0,y:sender.bounds.height+4),in:sender)
    }
    // A group title in a menu; macOS 13 has no section headers, so it gets a quiet disabled row.
    static func menuHeader(_ title:String)->NSMenuItem {
        if #available(macOS 14.0,*) {return NSMenuItem.sectionHeader(title:title)}
        let item=NSMenuItem(title:title,action:nil,keyEquivalent:"");item.isEnabled=false
        item.attributedTitle=NSAttributedString(string:title,attributes:[.font:NSFont.systemFont(ofSize:NSFont.smallSystemFontSize,weight:.semibold),.foregroundColor:NSColor.secondaryLabelColor])
        return item
    }
    @objc private func copySharePicture(_ item:NSMenuItem){
        guard let data=sharePicture(ShareCard.Style(rawValue:item.tag) ?? .card) else{NSSound.beep();return}
        NSPasteboard.general.clearContents()
        let copied=NSPasteboard.general.writeObjects([ShareCard.pasteboardItem(data)])
        showToast(copied ? "已复制" : "复制失败",ok:copied)
    }
    @objc private func openSharePicker(_ item:NSMenuItem){
        guard let sender=item.representedObject as? NSButton,sender.window != nil,let data=sharePicture(ShareCard.Style(rawValue:item.tag) ?? .card),let dashboard=snapshot?.dashboard else{NSSound.beep();return}
        let url=FileManager.default.temporaryDirectory.appendingPathComponent("Token Monitor "+dashboard.date+".png")
        guard (try? data.write(to:url,options:.atomic)) != nil else{NSSound.beep();return}
        let picker=NSSharingServicePicker(items:[url]);picker.delegate=self
        anchoredMenuOpen=true;hoverIntent.cancel();closeTask?.cancel();closeFlyouts(from:0)
        picker.show(relativeTo:sender.bounds,of:sender,preferredEdge:.minY)
    }
    func sharingServicePicker(_ picker:NSSharingServicePicker,didChoose service:NSSharingService?){
        anchoredMenuOpen=false;DispatchQueue.main.async{[weak self] in self?.reload(force:true)}
    }
    // A short confirmation on the panel surface; a rebuild re-places it until it expires.
    // Copy feedback stays in the header row so it never covers the numbers: the share button briefly
    // turns into a green check (orange on failure) and a short word sits in the gap left of the buttons.
    private func showToast(_ text:String,ok:Bool){
        toast=(text,Date().addingTimeInterval(1.8),ok);placeToast()
        DispatchQueue.main.asyncAfter(deadline:.now()+1.85){[weak self] in
            guard let self,let toast=self.toast,toast.until<=Date() else{return}
            self.toast=nil;self.placeToast()
        }
    }
    private func placeToast(){
        toastView?.removeFromSuperview();toastView=nil
        let current=toast.flatMap{$0.until>Date() ? $0 : nil}
        if let share=shareButton {
            // Bordered buttons ignore contentTintColor for their image, so the status icon carries its own colour.
            let base=NSImage.SymbolConfiguration(pointSize:11,weight:.semibold)
            if let current {
                let colour=current.ok ? statusGreen(share) : NSColor.systemOrange
                share.image=NSImage(systemSymbolName:current.ok ? "checkmark" : "exclamationmark",accessibilityDescription:current.text)?
                    .withSymbolConfiguration(base.applying(.init(paletteColors:[colour])))
            } else {share.image=NSImage(systemSymbolName:"square.and.arrow.up",accessibilityDescription:"分享用量图")?.withSymbolConfiguration(base)}
        }
        guard let current,let surface=panel.contentView as? Surface,let detect=detectButton,let row=detect.superview else{return}
        let label=NSTextField(labelWithString:current.text);label.font = .systemFont(ofSize:11,weight:.medium)
        label.textColor=current.ok ? statusGreen(surface) : .systemOrange
        let size=label.intrinsicContentSize,anchor=surface.convert(detect.bounds,from:detect)
        // Only in the gap after the heading; with a long heading the button alone carries the feedback.
        let heading=(snapshot?.dashboard.map(DashboardView.heading) ?? "Summary") as NSString
        let headingRight=surface.convert(NSPoint(x:12,y:0),from:row).x+ceil(heading.size(withAttributes:[.font:NSFont.systemFont(ofSize:14,weight:.semibold)]).width)+6
        // A little slack in the width keeps the last glyph from being clipped; the gap keeps it clear of the buttons.
        let width=ceil(size.width)+4,x=anchor.minX-12-width
        NSAccessibility.post(element:shareButton ?? surface,notification:.announcementRequested,userInfo:[.announcement:current.text])
        guard x>=headingRight else{return}
        label.frame=NSRect(x:x,y:anchor.midY-ceil(size.height)/2,width:width,height:ceil(size.height))
        surface.addSubview(label);toastView=label
    }
    @objc private func chooseRange(_ item:NSMenuItem){if let action=item.representedObject as? [String]{performAction(action)}}
    private func performAction(_ action:[String]){
        switch interaction.submit(action) {
        case .start:dispatchAction(action)
        case .queued:updatePresentation()
        case .ignored:break
        }
    }
    private func dispatchAction(_ action:[String]){
        let passive=["refresh-source","refresh-view","refresh","refresh-monitor","check-app-updates","set-range","retry-claude-usage"].contains(action[0])
        let keepOpen=PanelInteraction.keepsPanelOpen(action)
        if !keepOpen{hide()};if keepOpen && !passive{updatePresentation()}
        let id=UUID().uuidString,request=URL(fileURLWithPath:snapshotPath).deletingLastPathComponent().appendingPathComponent("panel-action.json")
        pendingAction=id
        do{
            let data=try JSONSerialization.data(withJSONObject:["id":id,"action":action,"createdAt":Date().timeIntervalSince1970])
            try data.write(to:request,options:.atomic)
            // The containing directory is private. A very fast SwiftBar can claim the request before chmod.
            try? FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:request.path)
            updateFallback()
            wakeSwiftBar()
            DispatchQueue.main.asyncAfter(deadline:.now()+0.8){[weak self] in
                guard let self,self.pendingAction==id,FileManager.default.fileExists(atPath:request.path) else{return}
                // Retry only the notification. SwiftBar atomically claims the same file at most once.
                self.wakeSwiftBar()
            }
            let task=DispatchWorkItem{[weak self] in
                guard let self,self.pendingAction==id else{return}
                self.checkAction()
                guard self.pendingAction==id else{return}
                let claimed = !FileManager.default.fileExists(atPath:request.path)
                guard PanelInteraction.mayExpire(action,claimed:claimed) else{return}
                if !claimed{try? FileManager.default.removeItem(at:request)}
                self.finishAction(id,failed:true)
            }
            actionTimeout=task
            let timeout:TimeInterval=PanelInteraction.automatic(action) ? 3 : PanelInteraction.isPreference(action) ? 20 : 8
            DispatchQueue.main.asyncAfter(deadline:.now()+timeout,execute:task)
        }catch{finishAction(id,failed:true)}
    }
    private func wakeSwiftBar(){
        var url=URLComponents();url.scheme="swiftbar";url.host="refreshplugin";url.queryItems=[URLQueryItem(name:"name",value:PanelInteraction.swiftBarName(pluginPath))]
        // Refresh without activating SwiftBar or taking focus from the panel.
        let quiet=NSWorkspace.OpenConfiguration();quiet.activates=false
        if let target=url.url{NSWorkspace.shared.open(target,configuration:quiet)}
    }
}
// Application entry point. The interaction tests compile the declarations above without launching the app.
UserDefaults.standard.setVolatileDomain(["AppleAccentColor":4,"AppleAquaColorVariant":1],forName:UserDefaults.argumentDomain)
let app=NSApplication.shared;app.setActivationPolicy(.accessory);let delegate=App();app.delegate=delegate;app.run()
