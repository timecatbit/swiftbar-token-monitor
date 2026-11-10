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
    var sessions:Int;var unpriced:Int;var summaryIncluded:Bool?;var counted:CountedUsage?;var models:[ModelUsage];var history:[Double]
    // What Summary adds for this source: nothing when it isn't counted, and only the requests no gateway
    // logged when a gateway (OpenCodex, Magpie) counts the rest. The card itself keeps every record.
    var summaryPart:ProviderUsage? {
        guard summaryIncluded != false else{return nil}
        guard let c=counted else{return self}
        var part=self;part.cost=c.cost;part.tokens=c.tokens;part.requests=c.requests;part.unpriced=c.unpriced;part.cache=c.cache;part.history=c.history
        return part
    }
}
struct Dashboard:Decodable {var date:String;var dates:[String];var providers:[ProviderUsage];var rangeKey:String;var rangeLabel:String;var chartUnit:String;var chartSpan:String;var chartStatus:String;var updated:String?;var chartPositions:[Double]?;var showSummary:Bool?;var allowCollapse:Bool?;var expandedProviders:[String]?}
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
// The share picture has its own layout: an opaque card on a soft gradient, never the panel's
// glass, because what shows through the glass depends on each user's desktop. It covers the
// usage that Summary counts; subscriptions, balances and keys never appear.
final class ShareCard:FlippedView {
    static let width:CGFloat=420,margin:CGFloat=20,inset:CGFloat=24,chartHeight:CGFloat=162,modelHeight:CGFloat=22
    let dashboard:Dashboard,project:Project
    private var rows:[ProviderUsage] {dashboard.providers}
    // Totals, chart and the share bar use what Summary counts; other sources are listed after them.
    private var counted:[ProviderUsage] {rows.compactMap{$0.summaryPart}}
    static func blockHeight(_ p:ProviderUsage)->CGFloat {60+CGFloat(max(1,p.models.count))*modelHeight+16}
    private var cardHeight:CGFloat {Self.inset+188+(dashboard.dates.count>1 ? Self.chartHeight : 0)+24+rows.reduce(0){$0+Self.blockHeight($1)}+Self.inset-20}
    init(_ dashboard:Dashboard,project:Project){
        // Every source with data, in the panel's order; those Summary doesn't count are tagged, not moved.
        var shown=dashboard
        shown.providers=dashboard.providers.filter{$0.available}
        self.dashboard=shown;self.project=project
        super.init(frame:.zero)
        frame=NSRect(x:0,y:0,width:Self.width,height:Self.margin+cardHeight+Self.margin+52)
    }
    required init?(coder:NSCoder){fatalError()}
    static func title(_ d:Dashboard)->String {
        ["today":"今日","24h":"近 24 小时","7d":"近 7 天","30d":"近 30 天","week":"本周","month":"本月","all":"累计"][d.rangeKey].map{$0+" AI 用量"} ?? d.rangeLabel+" AI 用量"
    }
    static func day(_ iso:String)->String {
        let parts=iso.split(separator:"-").compactMap{Int($0)}
        return parts.count==3 ? "\(parts[0]) 年 \(parts[1]) 月 \(parts[2]) 日" : iso
    }
    private func font(_ size:CGFloat,_ weight:NSFont.Weight,rounded:Bool=false)->NSFont {
        let base=NSFont.systemFont(ofSize:size,weight:weight)
        guard rounded,let descriptor=base.fontDescriptor.withDesign(.rounded) else{return base}
        return NSFont(descriptor:descriptor,size:size) ?? base
    }
    private func draw(_ value:String,_ rect:NSRect,_ font:NSFont,_ color:NSColor,_ align:NSTextAlignment = .left){
        let style=NSMutableParagraphStyle();style.alignment=align;style.lineBreakMode = .byTruncatingTail
        (value as NSString).draw(in:rect,withAttributes:[.font:font,.foregroundColor:color,.paragraphStyle:style])
    }
    private var teal:NSColor {dark(self) ? NSColor(srgbRed:0.43,green:0.79,blue:0.75,alpha:1) : NSColor(srgbRed:0.15,green:0.47,blue:0.46,alpha:1)}
    override func draw(_ dirtyRect:NSRect){
        let isDark=dark(self)
        let backdrop=NSGradient(starting:isDark ? NSColor(srgbRed:0.06,green:0.11,blue:0.11,alpha:1) : NSColor(srgbRed:0.90,green:0.96,blue:0.95,alpha:1),
                                ending:isDark ? NSColor(srgbRed:0.10,green:0.08,blue:0.14,alpha:1) : NSColor(srgbRed:0.95,green:0.93,blue:0.98,alpha:1))
        backdrop?.draw(in:bounds,angle:isDark ? -60 : 60)
        let card=NSRect(x:Self.margin,y:Self.margin,width:bounds.width-Self.margin*2,height:cardHeight),shape=NSBezierPath(roundedRect:card,xRadius:22,yRadius:22)
        NSGraphicsContext.saveGraphicsState()
        let shadow=NSShadow();shadow.shadowColor=NSColor.black.withAlphaComponent(isDark ? 0.35 : 0.08);shadow.shadowBlurRadius=24;shadow.shadowOffset=NSSize(width:0,height:-6);shadow.set()
        (isDark ? NSColor(srgbRed:0.11,green:0.12,blue:0.13,alpha:1) : .white).setFill();shape.fill()
        NSGraphicsContext.restoreGraphicsState()
        NSColor.labelColor.withAlphaComponent(isDark ? 0.10 : 0.06).setStroke();shape.lineWidth=1;shape.stroke()
        let left=card.minX+Self.inset,width=card.width-Self.inset*2,right=left+width
        var y=card.minY+Self.inset
        // Header: what and when.
        draw(Self.title(dashboard),NSRect(x:left,y:y,width:width*0.6,height:22),font(16,.semibold),.labelColor)
        let day=Self.day(dashboard.date),dayFont=font(11,.medium),dayWidth=ceil((day as NSString).size(withAttributes:[.font:dayFont]).width)+18
        let capsule=NSRect(x:right-dayWidth,y:y+1,width:dayWidth,height:20)
        teal.withAlphaComponent(isDark ? 0.18 : 0.10).setFill();NSBezierPath(roundedRect:capsule,xRadius:10,yRadius:10).fill()
        draw(day,capsule.insetBy(dx:9,dy:3),dayFont,teal,.center)
        // Hero cost, then three tiles.
        let counted=self.counted,costs=counted.compactMap{$0.cost},partial=counted.contains{$0.cost==nil || $0.unpriced>0}
        y+=40;draw(partial ? "已知 API 等值估算" : "API 等值估算",NSRect(x:left,y:y,width:width,height:16),font(11,.medium),.secondaryLabelColor)
        y+=16;draw(costs.isEmpty ? "—" : money(costs.reduce(0,+)),NSRect(x:left,y:y,width:width,height:50),font(40,.bold,rounded:true),.labelColor)
        y+=62
        let count=NumberFormatter();count.numberStyle = .decimal
        // Cache hit rate weighted by each tool's tokens; tools that report none are left out.
        let cached=counted.filter{$0.cache != nil},weight=cached.reduce(0){$0+$1.tokens}
        let hit=weight>0 ? String(format:"%.1f%%",cached.reduce(0){$0+($1.cache ?? 0)*$1.tokens}/weight*100) : "—"
        let tiles=[("Token",tokens(counted.reduce(0){$0+$1.tokens})),("请求",count.string(from:NSNumber(value:counted.reduce(0){$0+$1.requests})) ?? "0"),("缓存命中",hit)]
        let tileWidth=(width-20)/3
        for (i,(label,value)) in tiles.enumerated(){
            let tile=NSRect(x:left+CGFloat(i)*(tileWidth+10),y:y,width:tileWidth,height:56)
            teal.withAlphaComponent(isDark ? 0.10 : 0.06).setFill();NSBezierPath(roundedRect:tile,xRadius:12,yRadius:12).fill()
            draw(label,NSRect(x:tile.minX+12,y:tile.minY+9,width:tile.width-24,height:14),font(10.5,.medium),.secondaryLabelColor)
            draw(value,NSRect(x:tile.minX+12,y:tile.minY+25,width:tile.width-24,height:24),font(18,.semibold,rounded:true),.labelColor)
        }
        y+=70
        // Chart: the total as a soft area, each tool as a thin line in its own colour.
        let n=dashboard.dates.count
        if n>1 {
            let series=counted.map{p in (0..<n).map{i in i<p.history.count ? max(0,p.history[i]) : 0}}
            // Same scale as the panel: a rounded ceiling with labelled half and full grid lines.
            let observed=max((0..<n).map{i in series.reduce(0){$0+$1[i]}}.max() ?? 0,1),unit=pow(10,floor(log10(observed)))
            let ceiling=ceil(observed*1.08/unit*2)/2*unit
            let plot=NSRect(x:left,y:y+8,width:width,height:Self.chartHeight-46)
            for f in [CGFloat(0),0.5,1]{
                let yy=plot.maxY-f*plot.height
                line(NSPoint(x:plot.minX,y:yy),NSPoint(x:plot.maxX,y:yy),color:NSColor.labelColor.withAlphaComponent(f==0 ? 0.12 : 0.05),width:f==0 ? 1 : 0.5)
                if f>0{draw(axisTokens(ceiling*Double(f)),NSRect(x:left,y:yy-13,width:80,height:12),font(9,.medium),.tertiaryLabelColor)}
            }
            let positions=DashboardView.chartPositions(dashboard)
            let paths=DashboardView.smoothCurves(series,positions:positions,plot:plot,ceiling:ceiling),total=paths.last!
            let area=total.copy() as! NSBezierPath;area.line(to:NSPoint(x:plot.maxX,y:plot.maxY));area.line(to:NSPoint(x:plot.minX,y:plot.maxY));area.close()
            NSGraphicsContext.saveGraphicsState();area.addClip()
            NSGradient(starting:teal.withAlphaComponent(isDark ? 0.32 : 0.22),ending:teal.withAlphaComponent(0.0))?.draw(from:NSPoint(x:plot.midX,y:plot.minY),to:NSPoint(x:plot.midX,y:plot.maxY),options:[])
            NSGraphicsContext.restoreGraphicsState()
            for (i,curve) in paths.enumerated(){
                let isTotal=i==paths.count-1
                curve.lineWidth=isTotal ? 2.4 : 1.4;curve.lineCapStyle = .round;curve.lineJoinStyle = .round
                (isTotal ? teal : accent(counted[i].id,self).withAlphaComponent(0.9)).setStroke();curve.stroke()
            }
            let labelFont=font(9.5,.medium),middle=positions.indices.dropFirst().dropLast().min{abs(positions[$0]-0.5)<abs(positions[$1]-0.5)}
            for i in [0]+(middle.map{[$0]} ?? [])+[n-1]{
                let x=plot.minX+CGFloat(positions[i])*plot.width,align:NSTextAlignment=i==0 ? .left : (i==n-1 ? .right : .center)
                draw(dashboard.dates[i],NSRect(x:i==0 ? x : (i==n-1 ? x-80 : x-40),y:plot.maxY+7,width:80,height:13),labelFont,.tertiaryLabelColor,align)
            }
            y+=Self.chartHeight
        }
        // Share of tokens by tool, then each tool with its stats and every model, as on the panel.
        let sum=max(counted.reduce(0){$0+$1.tokens},1),bar=NSRect(x:left,y:y,width:width,height:8)
        NSGraphicsContext.saveGraphicsState();NSBezierPath(roundedRect:bar,xRadius:4,yRadius:4).addClip()
        NSColor.labelColor.withAlphaComponent(0.06).setFill();bar.fill()
        var x=bar.minX
        for p in counted{let w=bar.width*CGFloat(p.tokens/sum);accent(p.id,self).setFill();NSRect(x:x,y:bar.minY,width:max(0,w-1.5),height:bar.height).fill();x+=w}
        NSGraphicsContext.restoreGraphicsState()
        y+=24
        for (i,p) in rows.enumerated(){
            let c=accent(p.id,self),inSummary=p.summaryIncluded != false
            if i>0{line(NSPoint(x:left,y:y-10),NSPoint(x:right,y:y-10),color:NSColor.labelColor.withAlphaComponent(0.07),width:1)}
            c.setFill();NSBezierPath(ovalIn:NSRect(x:left,y:y+6,width:9,height:9)).fill()
            let nameFont=font(13.5,.semibold),nameWidth=min(width*0.5,ceil((p.name as NSString).size(withAttributes:[.font:nameFont]).width))
            draw(p.name,NSRect(x:left+18,y:y+1,width:nameWidth,height:18),nameFont,.labelColor)
            // A source a gateway partly counts says so, as the panel card does: its share is only the rest.
            if !inSummary || (p.counted != nil && !p.subtitle.isEmpty) {
                let tag=inSummary ? p.subtitle : "未计入 Summary",tagFont=font(9.5,.medium)
                let amount=ceil((headline(p) as NSString).size(withAttributes:[.font:font(15,.semibold,rounded:true)]).width)
                let room=right-amount-12-(left+26+nameWidth),tagWidth=min(room,ceil((tag as NSString).size(withAttributes:[.font:tagFont]).width)+12)
                let capsule=NSRect(x:left+26+nameWidth,y:y+2,width:tagWidth,height:16)
                NSColor.labelColor.withAlphaComponent(0.07).setFill();NSBezierPath(roundedRect:capsule,xRadius:8,yRadius:8).fill()
                draw(tag,capsule.insetBy(dx:6,dy:2),tagFont,.secondaryLabelColor,.center)
            }
            draw(headline(p),NSRect(x:right-130,y:y,width:130,height:20),font(15,.semibold,rounded:true),.labelColor,.right)
            let part=p.summaryPart?.tokens ?? 0
            draw(tokenLine(p)+(part>0 ? " · 占 "+share(part/sum) : ""),NSRect(x:left+18,y:y+21,width:width-18,height:15),font(11.5,.medium),.secondaryLabelColor)
            draw(DashboardView.stats(p),NSRect(x:left+18,y:y+38,width:width-18,height:15),font(10.5,.regular),.secondaryLabelColor)
            var my=y+60
            if p.models.isEmpty{draw("此时段暂无模型调用",NSRect(x:left+18,y:my,width:width-18,height:15),font(11,.regular),.secondaryLabelColor)}
            for m in p.models{
                draw(modelName(m.name),NSRect(x:left+18,y:my,width:width-150,height:15),font(11.5,.regular),.labelColor)
                draw(modelAmount(m),NSRect(x:right-130,y:my,width:80,height:15),font(11.5,.medium,rounded:true),.labelColor,.right)
                draw(share(m.share),NSRect(x:right-46,y:my,width:46,height:15),font(11,.regular),.secondaryLabelColor,.right)
                let track=NSRect(x:left+18,y:my+16,width:width-18,height:2.5)
                NSColor.labelColor.withAlphaComponent(0.06).setFill();NSBezierPath(roundedRect:track,xRadius:1.25,yRadius:1.25).fill()
                c.withAlphaComponent(0.85).setFill();NSBezierPath(roundedRect:NSRect(x:track.minX,y:track.minY,width:max(2.5,track.width*CGFloat(min(1,m.share))),height:track.height),xRadius:1.25,yRadius:1.25).fill()
                my+=Self.modelHeight
            }
            y+=Self.blockHeight(p)
        }
        // Signature under the card.
        let footer=card.maxY+16
        draw(project.name,NSRect(x:0,y:footer,width:bounds.width,height:18),font(13,.semibold),.labelColor,.center)
        draw(project.url,NSRect(x:0,y:footer+19,width:bounds.width,height:15),font(10.5,.regular),.secondaryLabelColor,.center)
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
final class DashboardView:FlippedView {
    var data:Dashboard;var toggleProvider:((String,Bool)->Void)?;private var hoveredProvider:String?
    private var summaryProviders:[ProviderUsage] {data.providers.compactMap{$0.summaryPart}}
    static func sectionHeight(_ p:ProviderUsage)->CGFloat {78+CGFloat(max(1,p.models.count))*18}
    static func providerHeight(_ p:ProviderUsage,_ d:Dashboard)->CGFloat {
        guard d.allowCollapse==true else{return sectionHeight(p)}
        return (d.expandedProviders ?? []).contains(p.id) ? 74+CGFloat(max(1,p.models.count))*18 : 52
    }
    // Legend names drop suffixes the cards below already show, so four fit on a row.
    static func legendLabels(_ d:Dashboard)->[String] {
        ["总量"]+d.providers.compactMap{$0.summaryPart}.map{p in
            p.id=="claude" ? "Claude" : p.name.hasSuffix(" Harness") ? String(p.name.dropLast(" Harness".count)) : p.name
        }
    }
    // The dashboard is always 344pt wide, so the legend gets 320pt between the margins.
    static func chartExtra(_ d:Dashboard)->CGFloat {CGFloat(max(0,legendLayout(legendLabels(d),width:320).count-1))*17}
    static func summaryHeight(_ d:Dashboard)->CGFloat {d.showSummary==false ? 44 : 230+chartExtra(d)}
    static func harnessHeaderHeight(_ d:Dashboard)->CGFloat {d.providers.isEmpty || d.showSummary==false ? 0 : 6}
    static func height(_ d:Dashboard)->CGFloat {summaryHeight(d)+harnessHeaderHeight(d)+d.providers.reduce(0){$0+providerHeight($1,d)}+(d.providers.isEmpty ? 0 : 4)}
    init(_ data:Dashboard,width:CGFloat){
        self.data=data;super.init(frame:NSRect(x:0,y:0,width:width,height:Self.height(data)))
        setAccessibilityElement(true);setAccessibilityRole(.group);setAccessibilityLabel("用量概览")
        if data.allowCollapse==true {
            var y=Self.summaryHeight(data)+Self.harnessHeaderHeight(data)
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
    override func draw(_ dirtyRect:NSRect){
        let left:CGFloat=12,right=bounds.width-12,width=right-left
        let valid=summaryProviders.filter{$0.available},costs=valid.compactMap{$0.cost}
        text(data.showSummary==false ? "Harness Token 用量" : "Summary",x:left,y:10,w:150,size:14,weight:.semibold)
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
        y+=Self.harnessHeaderHeight(data)
        for p in data.providers {if data.allowCollapse==true{drawCollapsibleProvider(p,y:y,left:left,right:right)}else{drawProvider(p,y:y,left:left,right:right)};y+=Self.providerHeight(p,data)}
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
            drawModels(p,y:y+70,left:left+12,right:right,color:c)
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
        drawModels(p,y:y+74,left:left,right:right,color:c)
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
        let totalColor=dark(self) ? NSColor(srgbRed:0.43,green:0.79,blue:0.75,alpha:1) : NSColor(srgbRed:0.15,green:0.47,blue:0.46,alpha:1)
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
    private func sharePicture()->Data? {
        guard let dashboard=snapshot?.dashboard else{return nil}
        let card=ShareCard(dashboard,project:snapshot?.project ?? Project(name:"Token Monitor",url:"https://github.com/timecatbit/swiftbar-token-monitor"))
        card.appearance=panel.effectiveAppearance
        return card.png()
    }
    // Copy lives in our own menu: the system share sheet runs in another process, and its clicks
    // used to look like clicks outside the panel, closing it before the copy ran.
    @objc private func shareDashboard(_ sender:NSButton){
        let menu=NSMenu();menu.autoenablesItems=false;menu.delegate=self
        for (title,symbol,action) in [("复制图片","doc.on.doc",#selector(copySharePicture)),("分享…","square.and.arrow.up",#selector(openSharePicker(_:)))]{
            let item=NSMenuItem(title:title,action:action,keyEquivalent:"");item.target=self;item.representedObject=sender
            item.image=NSImage(systemSymbolName:symbol,accessibilityDescription:nil);menu.addItem(item)
        }
        menu.popUp(positioning:nil,at:NSPoint(x:0,y:sender.bounds.height+4),in:sender)
    }
    @objc private func copySharePicture(){
        guard let data=sharePicture() else{NSSound.beep();return}
        NSPasteboard.general.clearContents()
        let copied=NSPasteboard.general.writeObjects([ShareCard.pasteboardItem(data)])
        showToast(copied ? "已复制" : "复制失败",ok:copied)
    }
    @objc private func openSharePicker(_ item:NSMenuItem){
        guard let sender=item.representedObject as? NSButton,sender.window != nil,let data=sharePicture(),let dashboard=snapshot?.dashboard else{NSSound.beep();return}
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
        let heading=(snapshot?.dashboard?.showSummary==false ? "Harness Token 用量" : "Summary") as NSString
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
