"""Small condition grammar; rejects unsupported shader syntax before native calls."""
import math
import re
import struct

from .core import FormatError

TOKEN=re.compile(r'\s*(?:(\d+(?:\.\d*)?(?:[eE][+-]?\d+)?[fF]?|\.\d+(?:[eE][+-]?\d+)?[fF]?)|([A-Za-z_][A-Za-z0-9_]*)|(&&|\|\||==|!=|<=|>=|[()!<>+-]))')


def f32(value):
    try:result=struct.unpack('<f',struct.pack('<f',value))[0]
    except (OverflowError,struct.error) as exc:raise FormatError('Condition value exceeds float32') from exc
    if not math.isfinite(result) or (result==0 and value!=0):raise FormatError('Condition value is nonfinite or underflows float32')
    return result


def parse(source):
    if not isinstance(source,str) or len(source)>2048:raise FormatError('Condition text exceeds 2048 characters')
    if not source.strip():return ('number',1.0)
    tokens=[];position=0
    while position<len(source):
        if not source[position:].strip():break
        match=TOKEN.match(source,position)
        if not match:raise FormatError('Unsupported condition syntax at character '+str(position))
        tokens.append(('number',f32(float(match[1].rstrip('fF')))) if match[1] is not None else ('name',match[2]) if match[2] is not None else ('op',match[3]))
        position=match.end()
        if len(tokens)>256:raise FormatError('Condition exceeds 256 tokens')
    index=0
    def expr(minimum=0,depth=0):
        nonlocal index
        if depth>32 or index>=len(tokens):raise FormatError('Condition is truncated or exceeds nesting bound')
        token=tokens[index];index+=1
        if token[0] in ('number','name'):left=token
        elif token==('op','('):
            left=expr(0,depth+1)
            if index>=len(tokens) or tokens[index]!=('op',')'):raise FormatError('Unbalanced condition parentheses')
            index+=1
        elif token[0]=='op' and token[1] in ('!','+','-'):left=('unary',token[1],expr(7,depth+1))
        else:raise FormatError('Condition operand is missing')
        precedence={'||':1,'&&':2,'==':3,'!=':3,'<':4,'>':4,'<=':4,'>=':4}
        while index<len(tokens) and tokens[index][0]=='op' and tokens[index][1] in precedence and precedence[tokens[index][1]]>=minimum:
            operator=tokens[index][1];index+=1;left=('binary',operator,left,expr(precedence[operator]+1,depth+1))
        return left
    tree=expr()
    if index!=len(tokens):raise FormatError('Condition has trailing tokens or an unsupported operator')
    return tree


def names(tree):
    if tree[0]=='name':return {tree[1]}
    if tree[0]=='unary':return names(tree[2])
    if tree[0]=='binary':return names(tree[2])|names(tree[3])
    return set()


def normalized(tree):
    if tree[0]=='number':
        text=format(tree[1],'.60f').rstrip('0').rstrip('.')
        text=text if text not in ('','-0') else '0'
        return text if '.' in text else text+'.0'
    if tree[0]=='name':return tree[1]
    if tree[0]=='unary':return '('+('' if tree[1]=='+' else tree[1])+normalized(tree[2])+')'
    return '('+normalized(tree[2])+tree[1]+normalized(tree[3])+')'


def evaluate(tree,values):
    if tree[0]=='number':return tree[1]
    if tree[0]=='name':
        if tree[1] not in values:raise FormatError('Condition variable is unavailable: '+tree[1])
        value=values[tree[1]]
        if type(value) not in (int,float,bool):raise FormatError('Condition variable is not scalar')
        return f32(float(value))
    if tree[0]=='unary':
        value=evaluate(tree[2],values)
        return float(not value) if tree[1]=='!' else -value if tree[1]=='-' else value
    a=evaluate(tree[2],values);b=evaluate(tree[3],values);op=tree[1]
    result={'&&':lambda:bool(a) and bool(b),'||':lambda:bool(a) or bool(b),'==':lambda:a==b,'!=':lambda:a!=b,
            '<':lambda:a<b,'>':lambda:a>b,'<=':lambda:a<=b,'>=':lambda:a>=b}[op]()
    return float(result)
