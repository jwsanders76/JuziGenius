"""
One-time repair for unlocked_words meanings frozen at the wrong text.

unlocked_words[word]["meaning"] (brain.json) is copied from words_freq.json
once, at the moment a word is unlocked (see analyze_text_compounds callers
and add_words_from_suggestions in juzi_engine.py) -- it is never read again
afterward. Fixing a wrong entry in words_freq.json therefore only helps
words unlocked *after* the fix; anyone who already unlocked the word keeps
whatever wrong text was there at the time, forever. This is the word
equivalent of backfill_character_meanings.py, needed because a September
2026 review of words_freq.json found two kinds of problems and fixed both
directly in that file:

  1. 10 words had drifted out of sync with the HSK "_with_sentences" CSVs
     (words_freq.json is a hand-built snapshot that doesn't regenerate from
     those CSVs automatically), so they still carried old, already-corrected
     wrong text (typos, wrong senses).
  2. 363 of the HSK 4-6 words (sourced from an external CC-CEDICT-derived
     vocabulary list) had raw dictionary-editor leftovers leaking into the
     English shown to users: bare classifiers ("份[fèn]"), alternate-
     pronunciation notes ("Taiwan pr. [...]" / "also pr. [...]"),
     traditional/simplified character pairs, and "sb"/"sth" dictionary
     shorthand. A handful had nothing else in their meaning at all once that
     noise was stripped, and got a real substitute definition instead of an
     empty string.

Unlike backfill_character_meanings.py, this can't just look for "blank" --
the corrections here replaced wrong-but-present text, not missing text. So
this script carries the exact old->new mapping for every word touched by
that review and only overwrites a stored meaning that matches the old text
byte-for-byte, leaving everything else untouched.

Usage:
    python3 backfill_word_meanings.py
"""
import json
import os

USERS_DIR = "users"
ROOT_BRAIN_PATH = "brain.json"

# word -> (old meaning, corrected meaning), from the words_freq.json fixes
# applied alongside the September 2026 HSK corpus/word-dictionary review.
WORD_MEANING_FIXES = {
    '杯': ('(counter for bottles)', '(counter for cups)'),
    '人': ('man', 'person'),
    '钱': ('coin', 'money'),
    '点': ("o'ckock", "o'clock; a bit; point"),
    '长': ('grow', 'long'),
    '非常': ('unusual', 'very; extremely'),
    '得': ('to have to', 'particle used after a verb'),
    '跑步': ('runing', 'running'),
    '爷爷': ('grandgather', 'grandfather'),
    '只': ('only', '(counter for birds and some animals)'),
    '暗': ('to close (a door); to eclipse; muddled; stupid; ignorant; variant of 暗[àn]', 'to close (a door); to eclipse; muddled; stupid; ignorant'),
    '份': ('classifier for gifts; newspaper; magazine; papers; reports; contracts etc; variant of 分[fèn]', 'classifier for gifts; newspaper; magazine; papers; reports; contracts etc; variant of 分'),
    '陪': ('to accompany; to keep sb company; to assist; old variant of 賠|赔[péi]', 'to accompany; to keep someone company; to assist; old variant of 赔'),
    '胡同': ('variant of 胡同[hú tòng]', 'hutong; lane; alley'),
    '克': ('variant of 克[kè]; to subdue; to overthrow; to restrain', 'to subdue; to overthrow; to restrain'),
    '平': ('flat; level; equal; to tie (make the same score); to draw (score); calm; peaceful; see also 平聲|平声[píng shēng]', 'flat; level; equal; to tie (make the same score); to draw (score); calm; peaceful; see also 平声'),
    '升': ('variant of 升[shēng]', 'liter (l); to rise; to ascend; to promote'),
    '铜': ('copper (chemistry); see also 紅銅|红铜[hóng tóng]', 'copper (chemistry); see also 红铜'),
    '祖国': ('ancestral land CL:個|个[gè]; homeland; used for PRC', 'ancestral land; homeland; used for PRC'),
    '别致': ('variant of 別緻|别致[bié zhì]', 'unique; novel; delicate and unusual in style'),
    '大伙儿': ('erhua variant of 大伙[dà huǒ]', 'erhua variant of 大伙'),
    '淡季': ('off season; slow business season; see also 旺季[wàng jì]', 'off season; slow business season; see also 旺季'),
    '得罪': ('to commit an offense; to violate the law; excuse me! (formal); see also 得罪[dé zui]', 'to commit an offense; to violate the law; excuse me! (formal)'),
    '法人': ('legal person; corporation; see also 自然人[zì rán rén]', 'legal person; corporation; see also 自然人'),
    '弥漫': ('variant of 彌漫|弥漫[mí màn]', 'to fill the air; to spread all over; to pervade'),
    '摊儿': ('erhua variant of 攤|摊[tān]', 'erhua variant of 摊'),
    '掏': ('variant of 掏[tāo]', 'to dig out; to scoop out; to fish out (e.g. from a pocket)'),
    '玩意儿': ('erhua variant of 玩意[wán yì]', 'erhua variant of 玩意'),
    '溪': ('variant of 溪; creek; rivulet', 'creek; rivulet'),
    '馅儿': ('erhua variant of 餡|馅; stuffing; filling; e.g. in 包子 or 饺子[jiǎo zi]', 'erhua variant of 馅; stuffing; filling; e.g. in 包子 or 饺子'),
    '凶恶': ('variant of 兇惡|凶恶; fierce; ferocious; fiendish; frightening', 'fierce; ferocious; fiendish; frightening'),
    '折': ('variant of 折[zhé]; to fold', 'to fold'),
    '上课': ('to attend class;', 'to attend class'),
    '介绍': ('to introduce (sb to sb)', 'to introduce (someone to someone)'),
    '关心': ('to care for sth', 'to care for something'),
    '报道': ('report; 份[fèn]', 'report'),
    '表格': ('form; table; 份[fèn]', 'form; table'),
    '饼干': ('biscuit; cracker; cookie; 塊|块[kuài]', 'biscuit; cracker; cookie'),
    '材料': ('material; data; makings; stuff; 種|种[zhǒng]', 'material; data; makings; stuff'),
    '成功': ('success; to succeed; 個|个[gè]', 'success; to succeed'),
    '成熟': ('mature; ripe; to mature; to ripen; Taiwan pr. [chéng shóu]', 'mature; ripe; to mature; to ripen'),
    '窗户': ('window; 扇[shàn]', 'window'),
    '词典': ('dictionary (of Chinese compound words); also written 辭典|辞典[cí diǎn]; 本[běn]', 'dictionary (of Chinese compound words); also written 辞典'),
    '大使馆': ('embassy; 個|个[gè]', 'embassy'),
    '代表': ('representative; delegate; 個|个; 名[míng]; to represent; to stand for; on behalf of; in the name of', 'representative; delegate; to represent; to stand for; on behalf of; in the name of'),
    '大夫': ('doctor; minister of state (in pre-Han states); 位[wèi]', 'doctor; minister of state (in pre-Han states)'),
    '调查': ('investigation; inquiry; to investigate; to survey; survey; (opinion) poll; 個|个[gè]', 'investigation; inquiry; to investigate; to survey; survey; (opinion) poll'),
    '断': ('to break; to snap; to cut off; to give up or abstain from sth; to judge; (usu. used in the negative) absolutely; definitely; decidedly', 'to break; to snap; to cut off; to give up or abstain from something; to judge; (usu. used in the negative) absolutely; definitely; decidedly'),
    '对': ('couple; pair; to be opposite; to oppose; to face; versus; for; to; correct (answer); to answer; to reply; to direct (towards sth); right', 'couple; pair; to be opposite; to oppose; to face; versus; for; to; correct (answer); to answer; to reply; to direct (towards something); right'),
    '法律': ('law; 套; 個|个[gè]', 'law'),
    '翻译': ('to translate; to interpret; translator; interpreter; translation; interpretation; 位; 名[míng]', 'to translate; to interpret; translator; interpreter; translation; interpretation'),
    '反映': ('to mirror; to reflect; mirror image; reflection; fig. to report; to make known; to render; used erroneously for 反應|反应; response or reaction', 'to mirror; to reflect; mirror image; reflection; fig. to report; to make known; to render; used erroneously for 反应; response or reaction'),
    '父亲': ('father; also pr. with light tone [fù qin]', 'father'),
    '感动': ('to move (sb); to touch (sb emotionally); moving', 'to move (someone); to touch (someone emotionally); moving'),
    '感情': ('feeling; emotion; sensation; likes and dislikes; deep affection for sb or sth; relationship (i.e. love affair); 種|种[zhǒng]', 'feeling; emotion; sensation; likes and dislikes; deep affection for someone or something; relationship (i.e. love affair)'),
    '干': ('tree trunk; main part of sth; to manage; to work; to do; capable; cadre; to kill (slang); to fuck (vulgar)', 'tree trunk; main part of something; to manage; to work; to do; capable; cadre; to kill (slang); to fuck (vulgar)'),
    '工资': ('wages; pay; 份; 月[yuè]', 'wages; pay'),
    '汗': ('perspiration; sweat; 頭|头; 身[shēn]; to be speechless (out of helplessness; embarrassment etc) (Internet slang used as an interjection)', 'perspiration; sweat; to be speechless (out of helplessness; embarrassment etc) (Internet slang used as an interjection)'),
    '好处': ('benefit; advantage; gain; profit; also pronounced [hǎo chù]', 'benefit; advantage; gain; profit'),
    '号码': ('number; 個|个[gè]', 'number'),
    '活动': ('to exercise; to move about; to operate; activity; loose; shaky; active; movable; maneuver; to use connections; 個|个[gè]', 'to exercise; to move about; to operate; activity; loose; shaky; active; movable; maneuver; to use connections'),
    '计划': ('plan; project; program; to plan; to map out; 項|项[xiàng]', 'plan; project; program; to plan; to map out'),
    '技术': ('technology; technique; skill; 種|种; 項|项[xiàng]', 'technology; technique; skill'),
    '家具': ('furniture; 套[tào]', 'furniture'),
    '交流': ('to exchange; exchange; communication; interaction; to have social contact (with sb)', 'to exchange; exchange; communication; interaction; to have social contact (with someone)'),
    '骄傲': ('pride; arrogance; conceited; proud of sth', 'pride; arrogance; conceited; proud of something'),
    '饺子': ('dumpling; pot-sticker; 隻|只[zhī]', 'dumpling; pot-sticker'),
    '教授': ('professor; to instruct; to lecture on; 位[wèi]', 'professor; to instruct; to lecture on'),
    '经历': ('experience; 次[cì]; to experience; to go through', 'experience; to experience; to go through'),
    '京剧': ('Beijing opera; 出[chū]', 'Beijing opera'),
    '镜子': ('mirror; 個|个[gè]', 'mirror'),
    '科学': ('science; scientific knowledge; scientific; 個|个; 種|种[zhǒng]', 'science; scientific knowledge; scientific'),
    '垃圾桶': ('rubbish bin; Taiwan pr. [lè sè tǒng]', 'rubbish bin'),
    '来不及': ("there's not enough time (to do sth); it's too late (to do sth)", "there's not enough time (to do something); it's too late (to do something)"),
    '来得及': ("there's still time; able to do sth in time", "there's still time; able to do something in time"),
    '俩': ('two (colloquial equivalent of 兩個|两个); both; some', 'two (colloquial equivalent of 两个); both; some'),
    '麻烦': ('inconvenient; troublesome; annoying; to trouble or bother sb; to put sb to trouble', 'inconvenient; troublesome; annoying; to trouble or bother someone; to put someone to trouble'),
    '梦': ('dream; 個|个[gè]', 'dream'),
    '母亲': ('mother; also pr. with light tone [mǔ qin]', 'mother'),
    '内容': ('content; substance; details; 項|项[xiàng]', 'content; substance; details'),
    '年龄': ("(a person's) age; 個|个[gè]", "(a person's) age"),
    '皮肤': ('skin; 塊|块[kuài]', 'skin'),
    '墙': ('wall; 堵[dǔ]', 'wall'),
    '敲': ('to hit; to strike; to tap; to rap; to knock; to rip sb off; to overcharge', 'to hit; to strike; to tap; to rap; to knock; to rip someone off; to overcharge'),
    '亲戚': ('a relative (i.e. family relation); 個|个; 位[wèi]', 'a relative (i.e. family relation)'),
    '情况': ('circumstances; state of affairs; situation; 種|种[zhǒng]', 'circumstances; state of affairs; situation'),
    '任务': ('mission; assignment; task; duty; role; 個|个[gè]', 'mission; assignment; task; duty; role'),
    '日记': ('diary; 本; 篇[piān]', 'diary'),
    '沙发': ('sofa; 張|张[zhāng]', 'sofa'),
    '申请': ('to apply for sth; application (form etc)', 'to apply for something; application (form etc)'),
    '师傅': ('master; qualified worker; respectful form of address for older men; 位; 名[míng]', 'master; qualified worker; respectful form of address for older men'),
    '狮子': ('lion; 頭|头[tóu]; Leo (star sign)', 'lion; Leo (star sign)'),
    '收入': ('to take in; income; revenue; 個|个[gè]', 'to take in; income; revenue'),
    '汤': ('soup; hot or boiling water; decoction of medicinal herbs; water in which sth has been boiled', 'soup; hot or boiling water; decoction of medicinal herbs; water in which something has been boiled'),
    '提前': ('to shift to an earlier date; to do sth ahead of time; in advance', 'to shift to an earlier date; to do something ahead of time; in advance'),
    '袜子': ('socks; stockings; 對|对; 雙|双[shuāng]', 'socks; stockings'),
    '文章': ('article; essay; literary works; writings; hidden meaning; 段; 頁|页[yè]', 'article; essay; literary works; writings; hidden meaning'),
    '小说': ('novel; fiction; 部[bù]', 'novel; fiction'),
    '信心': ('confidence; faith (in sb or sth)', 'confidence; faith (in someone or something)'),
    '血': ('blood; informal colloquial and Taiwan pr. [xiě]; also pr. [xuě]; 片[piàn]', 'blood'),
    '演出': ('to act (in a play); to perform; to put on (a performance); performance; concert; show; 次[cì]', 'to act (in a play); to perform; to put on (a performance); performance; concert; show'),
    '演员': ('actor or actress; performer; 位; 名[míng]', 'actor or actress; performer'),
    '意见': ('idea; opinion; suggestion; objection; complaint; 條|条[tiáo]', 'idea; opinion; suggestion; objection; complaint'),
    '由': ('to follow; from; it is for...to; reason; cause; because of; due to; to; to leave it (to sb); by (introduces passive verb)', 'to follow; from; it is for...to; reason; cause; because of; due to; to; to leave it (to someone); by (introduces passive verb)'),
    '语言': ('language; 種|种[zhǒng]', 'language'),
    '约会': ('appointment; engagement; date; 個|个[gè]; to arrange to meet', 'appointment; engagement; date; to arrange to meet'),
    '杂志': ('magazine; 份; 期[qī]', 'magazine'),
    '直接': ('direct; opposite: indirect 間接|间接; immediate; directly; straightforward', 'direct; opposite: indirect 间接; immediate; directly; straightforward'),
    '重视': ('to attach importance to sth; to value', 'to attach importance to something; to value'),
    '猪': ('hog; pig; swine; 頭|头[tóu]', 'hog; pig; swine'),
    '主动': ("to take the initiative; to do sth of one's own accord; spontaneous; active; opposite: passive 被動|被动[bèi dòng]; drive (of gears and shafts etc)", "to take the initiative; to do something of one's own accord; spontaneous; active; opposite: passive 被动; drive (of gears and shafts etc)"),
    '专业': ('specialty; specialized field; main field of study (at university); major; 個|个[gè]; professional', 'specialty; specialized field; main field of study (at university); major; professional'),
    '嘴': ('mouth; beak; nozzle; spout (of teapot etc); 個|个[gè]', 'mouth; beak; nozzle; spout (of teapot etc)'),
    '报告': ('to inform; to report; to make known; speech; talk; lecture; 份; 個|个; 通[tòng]', 'to inform; to report; to make known; speech; talk; lecture'),
    '本领': ('skill; ability; capability; 個|个[gè]', 'skill; ability; capability'),
    '辩论': ('debate; argument; to argue over; 次[cì]', 'debate; argument; to argue over'),
    '玻璃': ('glass; nylon; plastic; 塊|块[kuài]', 'glass; nylon; plastic'),
    '不好意思': ('to feel embarrassed; to find it embarrassing; to be sorry (for inconveniencing sb)', 'to feel embarrassed; to find it embarrassing; to be sorry (for inconveniencing someone)'),
    '参与': ('to participate (in sth)', 'to participate (in something)'),
    '餐厅': ('dining hall; dining room; restaurant; 家[jiā]', 'dining hall; dining room; restaurant'),
    '测验': ('test; to test; 個|个[gè]', 'test; to test'),
    '厕所': ('toilet; lavatory; 處|处[chù]', 'toilet; lavatory'),
    '炒': ('to sauté; to stir-fry; to speculate; to hype; to fire (sb)', 'to sauté; to stir-fry; to speculate; to hype; to fire (someone)'),
    '成语': ('Chinese set expression; often made up of 4 characters or two couplets of 4 characters each; often alluding to a story or historical quotation; idiom; proverb; saying; adage; set expression; 本; 句[jù]', 'Chinese set expression; often made up of 4 characters or two couplets of 4 characters each; often alluding to a story or historical quotation; idiom; proverb; saying; adage; set expression'),
    '翅膀': ('wing; 對|对[duì]', 'wing'),
    '传递': ('to transmit; to pass on to sb else', 'to transmit; to pass on to someone else'),
    '磁带': ('magnetic tape; 盒[hé]', 'magnetic tape'),
    '促使': ('to induce; to promote; to urge; to impel; to bring about; to provoke; to drive (sb to do sth); to catalyze; to actuate; to contribute to (some development)', 'to induce; to promote; to urge; to impel; to bring about; to provoke; to drive (someone to do something); to catalyze; to actuate; to contribute to (some development)'),
    '催': ('to urge; to press; to prompt; to rush sb; to hasten sth; to expedite', 'to urge; to press; to prompt; to rush someone; to hasten something; to expedite'),
    '打招呼': ('to greet sb by word or action; to give prior notice', 'to greet someone by word or action; to give prior notice'),
    '岛': ('island; 座[zuò]', 'island'),
    '递': ('to hand over; to pass on sth; to gradually increase or decrease; progressively', 'to hand over; to pass on something; to gradually increase or decrease; progressively'),
    '电池': ('battery; 組|组[zǔ]', 'battery'),
    '电台': ('transmitter-receiver; broadcasting station; radio station; 家[jiā]', 'transmitter-receiver; broadcasting station; radio station'),
    '对于': ('regarding; as far as sth is concerned; with regards to', 'regarding; as far as something is concerned; with regards to'),
    '吨': ('ton; Taiwan pr. [dùn]', 'ton'),
    '方案': ('plan; program (for action etc); proposal; proposed bill; 套[tào]', 'plan; program (for action etc); proposal; proposed bill'),
    '肥皂': ('soap; 條|条[tiáo]', 'soap'),
    '费用': ('cost; expenditure; expense; 個|个[gè]', 'cost; expenditure; expense'),
    '扶': ('to support with the hand; to help sb up; to support oneself by holding onto something; to help', 'to support with the hand; to help someone up; to support oneself by holding onto something; to help'),
    '改革': ('reform; 種|种; 項|项[xiàng]; to reform', 'reform; to reform'),
    '感想': ('impressions; reflections; thoughts; 個|个[gè]', 'impressions; reflections; thoughts'),
    '胳膊': ('arm; 條|条; 雙|双[shuāng]', 'arm'),
    '更加': ('more (than sth else); even more', 'more (than something else); even more'),
    '工厂': ('factory; 座[zuò]', 'factory'),
    '工程师': ('engineer; 位; 名[míng]', 'engineer'),
    '工人': ('worker; 名[míng]', 'worker'),
    '骨头': ('bone; 塊|块[kuài]; moral character; bitterness', 'bone; moral character; bitterness'),
    '光盘': ('compact disc; CD or DVD; CD ROM; 張|张[zhāng]', 'compact disc; CD or DVD; CD ROM'),
    '锅': ('pot; pan; boiler; 隻|只[zhī]', 'pot; pan; boiler'),
    '胡须': ('beard; 綹|绺[liǔ]', 'beard'),
    '火柴': ('match (for lighting fire); 盒[hé]', 'match (for lighting fire)'),
    '机器': ('machine; 部; 個|个[gè]', 'machine'),
    '家庭': ('family; household; 個|个[gè]', 'family; household'),
    '甲': ('first of the ten heavenly stems 十天干[shí tiān gān]; (used for an unspecified person or thing); first (in a list; as a party to a contract etc); armor plating; shell or carapace; (of the fingers or toes) nail; bladed leather or metal armor (old); ranking system used in the Imperial examinations (old); civil administration unit (old)', 'first of the ten heavenly stems 十天干; (used for an unspecified person or thing); first (in a list; as a party to a contract etc); armor plating; shell or carapace; (of the fingers or toes) nail; bladed leather or metal armor (old); ranking system used in the Imperial examinations (old); civil administration unit (old)'),
    '建议': ('to propose; to suggest; to recommend; proposal; suggestion; recommendation; 點|点[diǎn]', 'to propose; to suggest; to recommend; proposal; suggestion; recommendation'),
    '教练': ('instructor; sports coach; trainer; 位; 名[míng]', 'instructor; sports coach; trainer'),
    '教训': ('lesson; moral; to chide sb; to lecture sb', 'lesson; moral; to chide someone; to lecture someone'),
    '接待': ('to receive (a visitor); to admit (allow sb to enter)', 'to receive (a visitor); to admit (allow someone to enter)'),
    '接着': ("to catch and hold on; to continue; to go on to do sth; to follow; to carry on; then; after that; subsequently; to proceed; to ensue; in turn; in one's turn", "to catch and hold on; to continue; to go on to do something; to follow; to carry on; then; after that; subsequently; to proceed; to ensue; in turn; in one's turn"),
    '结构': ('structure; composition; makeup; architecture; 個|个[gè]', 'structure; composition; makeup; architecture'),
    '结账': ('to pay the bill; to settle accounts; also written 結帳|结帐', 'to pay the bill; to settle accounts; also written 结帐'),
    '开心': ('to feel happy; to rejoice; to have a great time; to make fun of sb', 'to feel happy; to rejoice; to have a great time; to make fun of someone'),
    '砍': ('to chop; to cut down; to throw sth at sb', 'to chop; to cut down; to throw something at someone'),
    '课程': ('course; academic program; 節|节; 門|门[mén]', 'course; academic program'),
    '矿泉水': ('mineral spring water; 杯[bēi]', 'mineral spring water'),
    '蜡烛': ('candle; 支[zhī]', 'candle'),
    '狼': ('wolf; 隻|只; 條|条[tiáo]', 'wolf'),
    '利益': ("benefit; (in sb's) interest", "benefit; (in someone's) interest"),
    '恋爱': ('(romantic) love; 場|场[chǎng]; in love; to have an affair', '(romantic) love; in love; to have an affair'),
    '临时': ('at the instant sth happens; temporary; interim; ad hoc', 'at the instant something happens; temporary; interim; ad hoc'),
    '领导': ('lead; leading; to lead; leadership; leader; 個|个[gè]', 'lead; leading; to lead; leadership; leader'),
    '骂': ('to scold; to abuse; 頓|顿[dùn]', 'to scold; to abuse'),
    '蜜蜂': ('bee; honeybee; 群[qún]', 'bee; honeybee'),
    '面临': ('to face sth; to be confronted with', 'to face something; to be confronted with'),
    '命令': ('order; command; 個|个[gè]', 'order; command'),
    '摩托车': ('motorbike; motorcycle; 部[bù]', 'motorbike; motorcycle'),
    '某': ('some; a certain; sb or sth indefinite; such-and-such', 'some; a certain; someone or something indefinite; such-and-such'),
    '木头': ('slow-witted; blockhead; log (of wood; timber etc); 根[gēn]', 'slow-witted; blockhead; log (of wood; timber etc)'),
    '脑袋': ('head; skull; brains; mental capability; 個|个[gè]', 'head; skull; brains; mental capability'),
    '年纪': ('age; 個|个[gè]', 'age'),
    '念': ("to read; to study (a degree course); to read aloud; to miss (sb); idea; remembrance; twenty (banker's anti-fraud numeral corresponding to 廿; 20)", "to read; to study (a degree course); to read aloud; to miss (someone); idea; remembrance; twenty (banker's anti-fraud numeral corresponding to 廿; 20)"),
    '牛仔裤': ('jeans; also written 牛崽褲|牛崽裤', 'jeans; also written 牛崽裤'),
    '女士': ('lady; madam; 位[wèi]; Miss; Ms', 'lady; madam; Miss; Ms'),
    '匹': ('classifier for horses; mules etc; Taiwan pr. [pī]; ordinary person; classifier for cloth: bolt; horsepower', 'classifier for horses; mules etc; ordinary person; classifier for cloth: bolt; horsepower'),
    '枪': ('gun; firearm; rifle; spear; thing with shape or function similar to a gun; 把; 杆; 條|条; 枝[zhī]; to substitute for another person in a test; to knock; classifier for rifle shots', 'gun; firearm; rifle; spear; thing with shape or function similar to a gun; to substitute for another person in a test; to knock; classifier for rifle shots'),
    '悄悄': ('quietly; secretly; stealthily; quiet; worried; Taiwan pr. [qiǎo qiǎo]', 'quietly; secretly; stealthily; quiet; worried'),
    '确定': ('definite; certain; fixed; to fix (on sth); to determine; to be sure; to ensure; to make certain; to ascertain; to clinch; to recognize; to confirm; OK (on computer dialog box)', 'definite; certain; fixed; to fix (on something); to determine; to be sure; to ensure; to make certain; to ascertain; to clinch; to recognize; to confirm; OK (on computer dialog box)'),
    '日历': ('calendar; 本[běn]', 'calendar'),
    '日用品': ('articles for daily use; 個|个[gè]', 'articles for daily use'),
    '上当': ("taken in (by sb's deceit); to be fooled; to be duped", "taken in (by someone's deceit); to be fooled; to be duped"),
    '舍不得': ('to hate to do sth; to hate to part with; to begrudge', 'to hate to do something; to hate to part with; to begrudge'),
    '诗': ('poem; poetry; verse; abbr. for Book of Songs 詩經|诗经[Shī Jīng]', 'poem; poetry; verse; abbr. for Book of Songs 诗经'),
    '实验': ('experiment; test; 次[cì]; experimental; to experiment', 'experiment; test; experimental; to experiment'),
    '试卷': ('examination paper; test paper; 張|张[zhāng]', 'examination paper; test paper'),
    '手套': ('glove; mitten; 隻|只[zhī]', 'glove; mitten'),
    '手续': ('procedure; 個|个[gè]; formalities', 'procedure; formalities'),
    '手指': ('finger; 隻|只[zhī]', 'finger'),
    '甩': ('to throw; to fling; to swing; to leave behind; to throw off; to dump (sb)', 'to throw; to fling; to swing; to leave behind; to throw off; to dump (someone)'),
    '说服': ('to persuade; to convince; to talk sb over; Taiwan pr. [shuì fú]', 'to persuade; to convince; to talk someone over'),
    '私人': ("private; personal; interpersonal; sb with whom one has a close personal relationship; a member of one's clique", "private; personal; interpersonal; someone with whom one has a close personal relationship; a member of one's clique"),
    '太太': ('married woman; Mrs.; Madam; wife; 位[wèi]', 'married woman; Mrs.; Madam; wife'),
    '痛快': ('overjoyed; delighted; happily; heartily; enjoying; also pr. [tòng kuai]', 'overjoyed; delighted; happily; heartily; enjoying'),
    '微笑': ('smile; 絲|丝[sī]; to smile', 'smile; to smile'),
    '委屈': ('to feel wronged; to cause sb to feel wronged; grievance', 'to feel wronged; to cause someone to feel wronged; grievance'),
    '无奈': ('helpless; without choice; for lack of better option; grudgingly; willy-nilly; nolens volens; abbr. for 無可奈何|无可奈何[wú kě nài hé]', 'helpless; without choice; for lack of better option; grudgingly; willy-nilly; nolens volens; abbr. for 无可奈何'),
    '雾': ('fog; mist; 陣|阵[zhèn]', 'fog; mist'),
    '下载': ('to download; also pr. [xià zài]', 'to download'),
    '现象': ('appearance; phenomenon; 種|种[zhǒng]', 'appearance; phenomenon'),
    '心脏': ('heart; 個|个[gè]', 'heart'),
    '选举': ('to elect; election; 個|个[gè]', 'to elect; election'),
    '宴会': ('banquet; feast; dinner party; 個|个[gè]', 'banquet; feast; dinner party'),
    '一旦': ('in case (sth happens); if; once (sth happens; then...); when; in a short time; in one day', 'in case (something happens); if; once (something happens; then...); when; in a short time; in one day'),
    '邮局': ('post office; 個|个[gè]', 'post office'),
    '与其': ('rather than...; 與其|与其 A 不如 B (rather than A; better to B)', 'rather than...; 与其 A 不如 B (rather than A; better to B)'),
    '粘贴': ('to stick; to affix; to adhere; to paste (as in cut; copy and paste); Taiwan pr. [nián tiē]; also written 黏貼|黏贴', 'to stick; to affix; to adhere; to paste (as in cut; copy and paste); also written 黏贴'),
    '展览': ('to put on display; to exhibit; exhibition; show; 次[cì]', 'to put on display; to exhibit; exhibition; show'),
    '战争': ('war; conflict; 次[cì]', 'war; conflict'),
    '掌握': ('to grasp (often fig.); to control; to seize (initiative; opportunity; destiny); to master; to know well; to understand sth well and know how to use it; fluency', 'to grasp (often fig.); to control; to seize (initiative; opportunity; destiny); to master; to know well; to understand something well and know how to use it; fluency'),
    '争论': ('to argue; to debate; to contend; argument; contention; controversy; debate; 場|场[chǎng]', 'to argue; to debate; to contend; argument; contention; controversy; debate'),
    '钟': ("clock; o'clock; time as measured in hours and minutes; bell; 座[zuò]", "clock; o'clock; time as measured in hours and minutes; bell"),
    '竹子': ('bamboo; 支; 根[gēn]', 'bamboo'),
    '主席': ('chairperson; premier; chairman; 位[wèi]', 'chairperson; premier; chairman'),
    '祝福': ('blessings; to wish sb well', 'blessings; to wish someone well'),
    '装': ('adornment; to adorn; dress; clothing; costume (of an actor in a play); to play a role; to pretend; to install; to fix; to wrap (sth in a bag); to load; to pack', 'adornment; to adorn; dress; clothing; costume (of an actor in a play); to play a role; to pretend; to install; to fix; to wrap (something in a bag); to load; to pack'),
    '资料': ('material; resources; data; information; profile (Internet); 個|个[gè]', 'material; resources; data; information; profile (Internet)'),
    '自豪': ('(feel a sense of) pride; to be proud of sth (in a good way)', '(feel a sense of) pride; to be proud of something (in a good way)'),
    '总理': ('premier; prime minister; 位; 名[míng]', 'premier; prime minister'),
    '总统': ('president (of a country); 位; 名; 屆|届[jiè]', 'president (of a country)'),
    '作品': ('work (of art); opus; 篇[piān]', 'work (of art); opus'),
    '作为': ("one's conduct; deed; activity; accomplishment; achievement; to act as; as (in the capacity of); qua; to view as; to look upon (sth as); to take sth to be", "one's conduct; deed; activity; accomplishment; achievement; to act as; as (in the capacity of); qua; to view as; to look upon (something as); to take something to be"),
    '爱不释手': ('to love sth too much to part with it (idiom); to fondle admiringly', 'to love something too much to part with it (idiom); to fondle admiringly'),
    '案件': ('law case; legal case; judicial case; 樁|桩; 起[qǐ]', 'law case; legal case; judicial case'),
    '把关': ('to guard a pass; to check on sth', 'to guard a pass; to check on something'),
    '拜年': ('pay a New Year call; wish sb a Happy New Year', 'pay a New Year call; wish someone a Happy New Year'),
    '拜托': ('to request sb to do sth; please!', 'to request someone to do something; please!'),
    '半途而废': ('to give up halfway (idiom); leave sth unfinished', 'to give up halfway (idiom); leave something unfinished'),
    '保密': ('to keep sth confidential; to maintain secrecy', 'to keep something confidential; to maintain secrecy'),
    '暴露': ('to expose; to reveal; to lay bare; also pr. [pù lù]', 'to expose; to reveal; to lay bare'),
    '鞭策': ('to spur on; to urge on; to encourage sb (e.g. to make progress)', 'to spur on; to urge on; to encourage someone (e.g. to make progress)'),
    '便条': ('(informal) note; 個|个[gè]', '(informal) note'),
    '辫子': ('plait; braid; pigtail; a mistake or shortcoming that may be exploited by an opponent; handle; 條|条[tiáo]', 'plait; braid; pigtail; a mistake or shortcoming that may be exploited by an opponent; handle'),
    '别墅': ('villa; 座[zuò]', 'villa'),
    '冰雹': ('hail; hailstone; 粒[lì]', 'hail; hailstone'),
    '不禁': ("can't help (doing sth); can't refrain from", "can't help (doing something); can't refrain from"),
    '不惜': ('not stint; not spare; not hesitate (to do sth); not scruple (to do sth)', 'not stint; not spare; not hesitate (to do something); not scruple (to do something)'),
    '裁判': ('judgment; to referee; umpire; judge; referee; 位; 名[míng]', 'judgment; to referee; umpire; judge; referee'),
    '钞票': ('paper money; a bill (e.g. 100 yuan); 扎[zā]', 'paper money; a bill (e.g. 100 yuan)'),
    '澄清': ('clear (of liquid); limpid; to clarify; to make sth clear; to be clear (about the facts)', 'clear (of liquid); limpid; to clarify; to make something clear; to be clear (about the facts)'),
    '抽空': ('to find the time to do sth', 'to find the time to do something'),
    '筹备': ('preparations; to get ready for sth', 'preparations; to get ready for something'),
    '处分': ('to discipline sb; to punish; disciplinary action; to deal with (a matter)', 'to discipline someone; to punish; disciplinary action; to deal with (a matter)'),
    '床单': ('bed sheet; 件; 張|张; 床[chuáng]', 'bed sheet'),
    '吹捧': ("to flatter; to laud sb's accomplishments; adulation", "to flatter; to laud someone's accomplishments; adulation"),
    '挫折': ('setback; reverse; check; defeat; frustration; disappointment; to frustrate; to discourage; to set sb back; to blunt; to subdue', 'setback; reverse; check; defeat; frustration; disappointment; to frustrate; to discourage; to set someone back; to blunt; to subdue'),
    '搭配': ('to pair up; to match; to arrange in pairs; to add sth into a group', 'to pair up; to match; to arrange in pairs; to add something into a group'),
    '打击': ('to hit; to strike; to attack; to crack down on sth; a setback; a blow; percussion (music)', 'to hit; to strike; to attack; to crack down on something; a setback; a blow; percussion (music)'),
    '打量': ('to size sb up; to take measure of; to suppose; to reckon', 'to size someone up; to take measure of; to suppose; to reckon'),
    '代理': ('to act on behalf of sb in a responsible position; to act as an agent or proxy; surrogate', 'to act on behalf of someone in a responsible position; to act as an agent or proxy; surrogate'),
    '当面': ("to sb's face; in sb's presence", "to someone's face; in someone's presence"),
    '捣乱': ('to disturb; to look for trouble; to stir up a row; to bother sb intentionally', 'to disturb; to look for trouble; to stir up a row; to bother someone intentionally'),
    '蹬': ('to step on; to tread on; to wear; Taiwan pr. [dèng]', 'to step on; to tread on; to wear'),
    '垫': ('pad; cushion; mat; to pad out; to fill a gap; to pay for sb; to advance (money)', 'pad; cushion; mat; to pad out; to fill a gap; to pay for someone; to advance (money)'),
    '跌': ('to drop; to fall; to tumble; Taiwan pr. [dié]', 'to drop; to fall; to tumble'),
    '盯': ('to watch attentively; to fix attention on; to stare; to gaze; to follow; to shadow sb', 'to watch attentively; to fix attention on; to stare; to gaze; to follow; to shadow someone'),
    '动员': ('to mobilize; to arouse; mobilization; 個|个[gè]', 'to mobilize; to arouse; mobilization'),
    '端': ('end; extremity; item; port; to hold sth level with both hands; to carry; regular', 'end; extremity; item; port; to hold something level with both hands; to carry; regular'),
    '对立': ('to oppose; to set sth against; to be antagonistic to; antithetical; relative opposite; opposing; diametrical', 'to oppose; to set something against; to be antagonistic to; antithetical; relative opposite; opposing; diametrical'),
    '对应': ('to correspond; a correspondence; corresponding; homologous; matching with sth; counterpart', 'to correspond; a correspondence; corresponding; homologous; matching with something; counterpart'),
    '耳环': ('earring; 對|对[duì]', 'earring'),
    '反思': ('to think back over sth; to review; to revisit; to rethink; reflection; reassessment', 'to think back over something; to review; to revisit; to rethink; reflection; reassessment'),
    '敷衍': ('to elaborate (on a theme); to expound (the classics); perfunctory; to skimp; to botch; to do sth half-heartedly or just for show; barely enough to get by', 'to elaborate (on a theme); to expound (the classics); perfunctory; to skimp; to botch; to do something half-heartedly or just for show; barely enough to get by'),
    '副': ('secondary; auxiliary; deputy; assistant; vice-; abbr. for 副詞|副词 adverb; classifier for pairs; sets of things & facial expressions', 'secondary; auxiliary; deputy; assistant; vice-; abbr. for 副词 adverb; classifier for pairs; sets of things & facial expressions'),
    '附和': ("to parrot; to crib; to copy sb's action or words; to trail sb's footsteps; copy-cat", "to parrot; to crib; to copy someone's action or words; to trail someone's footsteps; copy-cat"),
    '盖章': ('to affix a seal (to sth)', 'to affix a seal (to something)'),
    '干劲': ('enthusiasm for doing sth', 'enthusiasm for doing something'),
    '高考': ('college entrance exam (abbr. for 普通高等學校招生全國統一考試|普通高等学校招生全国统一考试); entrance exam for senior government service posts (Taiwan)', 'college entrance exam (abbr. for 普通高等学校招生全国统一考试); entrance exam for senior government service posts (Taiwan)'),
    '跟踪': ("to follow sb's tracks; to tail; to shadow", "to follow someone's tracks; to tail; to shadow"),
    '共鸣': ('resonance (physics); sympathetic response to sth', 'resonance (physics); sympathetic response to something'),
    '固有': ('intrinsic to sth; inherent; native', 'intrinsic to something; inherent; native'),
    '规划': ('to plan (how to do sth); planning; plan; program', 'to plan (how to do something); planning; plan; program'),
    '归还': ('to return sth; to revert', 'to return something; to revert'),
    '过瘾': ('to satisfy a craving; to get a kick out of sth; gratifying; immensely enjoyable; satisfying; fulfilling', 'to satisfy a craving; to get a kick out of something; gratifying; immensely enjoyable; satisfying; fulfilling'),
    '恨不得': ("wishing one could do sth; to hate to be unable; itching to do sth; can't wait for; to wish one could do sth; to desire strongly", "wishing one could do something; to hate to be unable; itching to do something; can't wait for; to wish one could do something; to desire strongly"),
    '呼吁': ('to call on (sb to do sth); to appeal (to); an appeal', 'to call on (someone to do something); to appeal (to); an appeal'),
    '华侨': ('overseas Chinese; (in a restricted sense) Chinese emigrant who still retains Chinese nationality; 位; 名[míng]', 'overseas Chinese; (in a restricted sense) Chinese emigrant who still retains Chinese nationality'),
    '画蛇添足': ('lit. draw legs on a snake (idiom); fig. to ruin the effect by adding sth superfluous; to overdo it', 'lit. draw legs on a snake (idiom); fig. to ruin the effect by adding something superfluous; to overdo it'),
    '回避': ('to shun; to avoid (sb); to skirt; to evade (an issue); to step back; to withdraw; to recuse (a judge etc)', 'to shun; to avoid (someone); to skirt; to evade (an issue); to step back; to withdraw; to recuse (a judge etc)'),
    '活该': ('(coll.) serve sb right; deservedly; ought; should', '(coll.) serve someone right; deservedly; ought; should'),
    '寄托': ('to have sb look after sb; to entrust the care of sb; to place (hope etc) on', 'to have someone look after someone; to entrust the care of someone; to place (hope etc) on'),
    '简体字': ('simplified Chinese character; as opposed to traditional Chinese character 繁體字|繁体字[fán tǐ zì]', 'simplified Chinese character; as opposed to traditional Chinese character 繁体字'),
    '将军': ('general; high-ranking military officer; to check or checkmate; fig. to embarrass; to challenge; to put sb on the spot', 'general; high-ranking military officer; to check or checkmate; fig. to embarrass; to challenge; to put someone on the spot'),
    '交代': ('to hand over; to explain; to make clear; to brief (sb); to account for; to justify oneself; to confess; to finish (colloquial)', 'to hand over; to explain; to make clear; to brief (someone); to account for; to justify oneself; to confess; to finish (colloquial)'),
    '较量': ('to have a contest with sb; to cross swords; to measure up against; to compete with; to haggle; to quibble', 'to have a contest with someone; to cross swords; to measure up against; to compete with; to haggle; to quibble'),
    '解除': ('to remove; to sack; to get rid of; to relieve (sb of their duties); to free; to lift (an embargo); to rescind (an agreement)', 'to remove; to sack; to get rid of; to relieve (someone of their duties); to free; to lift (an embargo); to rescind (an agreement)'),
    '精益求精': ('to perfect sth that is already outstanding (idiom); constantly improving', 'to perfect something that is already outstanding (idiom); constantly improving'),
    '拘留': ('to detain (a prisoner); to keep sb in custody', 'to detain (a prisoner); to keep someone in custody'),
    '局限': ('to limit; to confine; to restrict sth within set boundaries', 'to limit; to confine; to restrict something within set boundaries'),
    '军队': ('army troops; 個|个[gè]', 'army troops'),
    '亏待': ('to treat sb unfairly', 'to treat someone unfairly'),
    '昆虫': ('insect; 群; 堆[duī]', 'insect'),
    '喇叭': ('horn (automobile etc); loudspeaker; brass wind instrument; trumpet; suona 鎖吶|锁呐[suǒ nà]', 'horn (automobile etc); loudspeaker; brass wind instrument; trumpet; suona 锁呐'),
    '乐意': ('to be willing to do sth; to be ready to do sth; to be happy to do sth; content; satisfied', 'to be willing to do something; to be ready to do something; to be happy to do something; content; satisfied'),
    '领袖': ('leader; 位; 名[míng]', 'leader'),
    '啰唆': ('see 囉嗦|啰嗦[luō suo]', 'see 啰嗦'),
    '掠夺': ('to plunder; to rob; also written 略奪|略夺', 'to plunder; to rob; also written 略夺'),
    '麻醉': ("anesthesia; fig. to poison (sb's mind)", "anesthesia; fig. to poison (someone's mind)"),
    '嘛': ('modal particle indicating that sth is obvious; particle indicating a pause for emphasis', 'modal particle indicating that something is obvious; particle indicating a pause for emphasis'),
    '迷信': ('superstition; to have a superstitious belief (in sth)', 'superstition; to have a superstitious belief (in something)'),
    '勉强': ('to do with difficulty; to force sb to do sth; reluctant; barely enough', 'to do with difficulty; to force someone to do something; reluctant; barely enough'),
    '哦': ('oh (interjection indicating that one has just learned sth)', 'oh (interjection indicating that one has just learned something)'),
    '抛弃': ('to abandon; to discard; to renounce; to dump (sb)', 'to abandon; to discard; to renounce; to dump (someone)'),
    '偏偏': ('(indicates that sth turns out just the opposite of what one would expect or what would be normal); unfortunately; against expectations', '(indicates that something turns out just the opposite of what one would expect or what would be normal); unfortunately; against expectations'),
    '迁就': ('to yield; to adapt to; to accommodate to (sth)', 'to yield; to adapt to; to accommodate to (something)'),
    '潜水': ('to dive; to go under water; lurker (Internet slang for sb who reads forum posts but never replies)', 'to dive; to go under water; lurker (Internet slang for someone who reads forum posts but never replies)'),
    '锲而不舍': ('to chip away at a task and not abandon it (idiom); to chisel away at sth; to persevere; unflagging efforts', 'to chip away at a task and not abandon it (idiom); to chisel away at something; to persevere; unflagging efforts'),
    '钦佩': ('to admire; to look up to; to respect sb greatly', 'to admire; to look up to; to respect someone greatly'),
    '人家': ('other people; sb else; he; she or they; I; me (referring to oneself as "one" or "people")', 'other people; someone else; he; she or they; I; me (referring to oneself as "one" or "people")'),
    '认定': ("to maintain (that sth is true); to determine (a fact); determination (of an amount); of the firm opinion; to believe firmly; to set one's mind on; to identify with", "to maintain (that something is true); to determine (a fact); determination (of an amount); of the firm opinion; to believe firmly; to set one's mind on; to identify with"),
    '啥': ('dialectal equivalent of 什麼|什么[shén me]', 'dialectal equivalent of 什么'),
    '捎': ('to bring sth to sb; to deliver', 'to bring something to someone; to deliver'),
    '审判': ('a trial; to try sb', 'a trial; to try someone'),
    '声明': ('statement; declaration; 份[fèn]', 'statement; declaration'),
    '试验': ('experiment; test; experimental; 個|个[gè]', 'experiment; test; experimental'),
    '示范': ('to demonstrate; to show how to do sth; demonstration; a model example', 'to demonstrate; to show how to do something; demonstration; a model example'),
    '示意': ('to hint; to indicate (an idea to sb)', 'to hint; to indicate (an idea to someone)'),
    '事故': ('accident; 起; 次[cì]', 'accident'),
    '涮': ('to rinse; to trick; to fool sb; to cook by dipping finely sliced ingredients briefly in boiling water or soup (generally done at the dining table)', 'to rinse; to trick; to fool someone; to cook by dipping finely sliced ingredients briefly in boiling water or soup (generally done at the dining table)'),
    '艘': ('classifier for ships; Taiwan pr. [sāo]', 'classifier for ships'),
    '搜索': ('to search; to look for sth; to scour (search meticulously); to look sth up; internet search; database search', 'to search; to look for something; to scour (search meticulously); to look something up; internet search; database search'),
    '提示': ('to prompt; to present; to point out; to draw attention to sth; hint; brief; cue', 'to prompt; to present; to point out; to draw attention to something; hint; brief; cue'),
    '投掷': ('to throw sth a long distance; to hurl; to throw at; to throw (dice etc); to flip (a coin)', 'to throw something a long distance; to hurl; to throw at; to throw (dice etc); to flip (a coin)'),
    '惋惜': ('to feel sorry for a person over sth that should have happened', 'to feel sorry for a person over something that should have happened'),
    '文物': ('cultural relic; historical relic; 個|个[gè]', 'cultural relic; historical relic'),
    '诬陷': ('to entrap; to frame; to plant false evidence against sb', 'to entrap; to frame; to plant false evidence against someone'),
    '无从': ("not to have access; beyond one's authority or capability; sth one has no way of doing", "not to have access; beyond one's authority or capability; something one has no way of doing"),
    '无可奈何': ('have no way out; have no alternative; abbr. to 無奈|无奈[wú nài]', 'have no way out; have no alternative; abbr. to 无奈'),
    '牺牲': ("to sacrifice oneself; to lay down one's life; to do sth at the expense of; beast slaughtered for sacrifice; sacrifice", "to sacrifice oneself; to lay down one's life; to do something at the expense of; beast slaughtered for sacrifice; sacrifice"),
    '携带': ("to carry (on one's person); to support (old); Taiwan pr. [xī dài]", "to carry (on one's person); to support (old)"),
    '协会': ('an association; a society; 家[jiā]', 'an association; a society'),
    '须知': ('prerequisites; rules that must be known before starting sth', 'prerequisites; rules that must be known before starting something'),
    '悬念': ("suspense in a movie; play etc; concern for sb's welfare", "suspense in a movie; play etc; concern for someone's welfare"),
    '学位': ('academic degree; e.g.: BSc 學士學位|学士学位; MSc 碩士學位|硕士学位; Diploma 學位證書|学位证书; PhD 博士學位|博士学位[bó shì xué wèi]', 'academic degree; e.g.: BSc 学士学位; MSc 硕士学位; Diploma 学位证书; PhD 博士学位'),
    '厌恶': ('to loathe; to hate; disgusted with sth', 'to loathe; to hate; disgusted with something'),
    '要命': ("to cause sb's death; very; extremely; frightening; annoying", "to cause someone's death; very; extremely; frightening; annoying"),
    '依靠': ('to rely on sth (for support etc); to depend on', 'to rely on something (for support etc); to depend on'),
    '以至': ('down to; up to; to such an extent as to ... (also written 以至於|以至于)', 'down to; up to; to such an extent as to ... (also written 以至于)'),
    '隐患': ('a danger concealed within sth; hidden damage; misfortune not visible from the surface', 'a danger concealed within something; hidden damage; misfortune not visible from the surface'),
    '应邀': ("at sb's invitation; on invitation", "at someone's invitation; on invitation"),
    '元宵节': ('Lantern Festival; the final event of the Spring Festival 春節|春节; on 15th of first month of the lunar calendar', 'Lantern Festival; the final event of the Spring Festival 春节; on 15th of first month of the lunar calendar'),
    '咋': ('dialectal equivalent of 怎麼|怎么[zěn me]', 'dialectal equivalent of 怎么'),
    '糟蹋': ('to waste; to defile; to abuse; to insult; to defile; to trample on; to wreck; also pr. [zāo ta]', 'to waste; to defile; to abuse; to insult; to defile; to trample on; to wreck'),
    '沾光': ('to bask in the light; fig. to benefit from association with sb or sth; reflected glory', 'to bask in the light; fig. to benefit from association with someone or something; reflected glory'),
    '展示': ('to reveal; to display; to show; to exhibit sth', 'to reveal; to display; to show; to exhibit something'),
    '战斗': ('to fight; to battle; 次[cì]', 'to fight; to battle'),
    '帐篷': ('tent; 座[zuò]', 'tent'),
    '照料': ('to tend; to take care of sb', 'to tend; to take care of someone'),
    '折腾': ('to toss from side to side (e.g. sleeplessly); to repeat sth over and over again; to torment sb; to play crazy', 'to toss from side to side (e.g. sleeplessly); to repeat something over and over again; to torment someone; to play crazy'),
    '真相': ('the truth about sth; the actual facts', 'the truth about something; the actual facts'),
    '争气': ('to work hard for sth; to resolve on improvement; determined not to fall short', 'to work hard for something; to resolve on improvement; determined not to fall short'),
    '证实': ('to confirm (sth to be true); to verify', 'to confirm (something to be true); to verify'),
    '指望': ('to hope for sth; to count on; hope', 'to hope for something; to count on; hope'),
    '致辞': ('to express in words or writing; to make a speech (esp. short introduction; vote of thanks; afterword; funeral homily etc); to address (an audience); same as 致詞|致词', 'to express in words or writing; to make a speech (esp. short introduction; vote of thanks; afterword; funeral homily etc); to address (an audience); same as 致词'),
    '种子': ('seed; 粒[lì]', 'seed'),
    '众所周知': ('see 眾所周知|众所周知[zhòng suǒ zhōu zhī]', 'as is well known; as everyone knows (idiom)'),
    '周边': ('periphery; rim; also written 周邊|周边', 'periphery; rim; also written 周边'),
    '周转': ('turnover (in cash or personnel); to have enough resources to cover a need; also written 周轉|周转', 'turnover (in cash or personnel); to have enough resources to cover a need; also written 周转'),
    '拽': ('to pull; to tug at (sth)', 'to pull; to tug at (something)'),
    '传记': ('biography; 部[bù]', 'biography'),
    '子弹': ('bullet; 顆|颗; 發|发[fā]', 'bullet'),
    '纵横': ('lit. warp and weft in weaving; vertically and horizontal; length and breadth; criss-crossed; able to move unhindered; abbr. for 合縱連橫|合纵连横; School of Diplomacy during the Warring States Period (475-221 BC)', 'lit. warp and weft in weaving; vertically and horizontal; length and breadth; criss-crossed; able to move unhindered; abbr. for 合纵连横; School of Diplomacy during the Warring States Period (475-221 BC)'),
    '阻挠': ('to thwart; to obstruct (sth)', 'to thwart; to obstruct (something)'),
    '颇': ('rather; quite; considerably (Taiwan pr. ); oblique; inclined; slanting', 'rather; quite; considerably; oblique; inclined; slanting'),
}


def find_brain_paths():
    paths = []
    if os.path.exists(ROOT_BRAIN_PATH):
        paths.append(ROOT_BRAIN_PATH)
    if os.path.isdir(USERS_DIR):
        for entry in sorted(os.listdir(USERS_DIR)):
            brain_path = os.path.join(USERS_DIR, entry, "brain.json")
            if os.path.isfile(brain_path):
                paths.append(brain_path)
    return paths


def backfill(brain_path):
    with open(brain_path, "r", encoding="utf-8") as f:
        brain_data = json.load(f)

    unlocked_words = brain_data.get("unlocked_words") or {}
    fixed = []
    for word, meta in unlocked_words.items():
        fix = WORD_MEANING_FIXES.get(word)
        if not fix:
            continue
        old_meaning, new_meaning = fix
        if meta.get("meaning") == old_meaning:
            meta["meaning"] = new_meaning
            fixed.append(word)

    if fixed:
        with open(brain_path, "w", encoding="utf-8") as f:
            json.dump(brain_data, f, ensure_ascii=False, indent=4)
    return fixed


def main():
    total_fixed = 0
    for brain_path in find_brain_paths():
        fixed = backfill(brain_path)
        if fixed:
            print(f"{brain_path}: fixed {', '.join(fixed)}")
            total_fixed += len(fixed)

    print(f"Done. {total_fixed} word meaning(s) backfilled." if total_fixed
          else "Done. Nothing to backfill.")


if __name__ == "__main__":
    main()
