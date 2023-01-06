import hashlib
import marshal
import random
import sys
from _ast import Tuple, List, Call, Name, Load, Attribute, Constant, FunctionDef, Assign, Store, Starred, Subscript, Slice, arguments, BinOp, Mult, \
    Return, AST, Module, Expr, Pass
from ast import fix_missing_locations
from types import CodeType
from typing import Any, Callable

from Crypto.Cipher import AES

from . import Transformer, rnd_name, ast_import_from, ast_import_full
from ..cfg import ConfigValue
from ..util import randomize_cache


class ConstructDynamicCodeObject(Transformer):
    _ctype_arg_names = [
        "co_argcount",
        "co_posonlyargcount",
        "co_kwonlyargcount",
        "co_nlocals",
        "co_stacksize",
        ("co_flags", 0),
        "co_code",
        "co_consts",
        "co_names",
        "co_varnames",
        "co_filename",
        ("co_name", ""),
        ("co_qualname", ""),
        ("co_firstlineno", 0),
        ("co_linetable", b""),
        "co_exceptiontable",
        "co_freevars",
        "co_cellvars"
    ]

    def __init__(self):
        self.code_obj_dict = dict()
        super().__init__("dynamicCodeObjLauncher",
                         "Launches the program by constructing it from the ground up with dynamic code objects. This REQUIRES PYTHON 3.11",
                         encrypt=ConfigValue("Encrypts the bytecode with a dynamically generated AES key", True))

    def get_all_code_objects(self, args):
        all_cos = []
        for x in args:
            if isinstance(x, list) or isinstance(x, tuple):
                all_cos = [*self.get_all_code_objects(x), *all_cos]
            elif isinstance(x, CodeType):
                all_cos.append(x)
                all_cos = [*self.get_all_code_objects(self.args_from_co(x)), *all_cos]
        return all_cos

    def args_from_co(self, code: CodeType):
        return [getattr(code, x) if not isinstance(x, tuple) else x[1] for x in self._ctype_arg_names]

    def _parse_const(self, el: Any, ctx):
        if isinstance(el, tuple):
            return Tuple(
                elts=[self._parse_const(x, ctx) for x in el],
                ctx=ctx
            )
        elif isinstance(el, list):
            return List(
                elts=[self._parse_const(x, ctx) for x in el],
                ctx=ctx
            )
        elif isinstance(el, CodeType):
            if el in self.code_obj_dict:  # we have a generator for this, use it
                return Call(
                    func=Name(self.code_obj_dict[el], Load()),
                    args=[],
                    keywords=[]
                )
            else:  # we dont have a generator? alright then, just marshal it
                b = marshal.dumps(el)
                return Call(
                    func=Attribute(
                        value=Call(
                            func=Name('__import__', Load()),
                            args=[
                                Constant("marshal")
                            ],
                            keywords=[]
                        ),
                        attr="loads",
                        ctx=Load()
                    ),
                    args=[
                        Constant(b)
                    ],
                    keywords=[]
                )
        else:
            return Constant(el)

    def create_code_obj_loader(self, func_name: str, compiled_code_obj: CodeType,
                               process_bytecode: Callable[[list], None] = lambda x: randomize_cache(x)) -> FunctionDef:
        collected_args = self.args_from_co(compiled_code_obj)
        co_code_index = self._ctype_arg_names.index("co_code")
        co_code = collected_args[co_code_index]
        co_code_l = list(co_code)

        process_bytecode(co_code_l)

        collected_args[co_code_index] = bytes(co_code_l)
        loader_asm = []
        for i in range(len(collected_args)):  # go over each code object arg
            v = collected_args[i]
            if i > 0 and collected_args[i - 1] == collected_args[i]:  # is the one below us the same as this one?
                elm: list = loader_asm[
                    len(loader_asm) - 1].value.elts  # then expand the assignment to include us aswell
                elm.insert(2, self._parse_const(v, Load()))
                elm[len(elm) - 1].value.slice.lower.value += 1
            else:  # if not, make the assignment
                ass_statement = Assign(  # a = [*a[:i], <arg>, *a[i+1:]] -> insert us at i
                    targets=[Name('a', Store())],
                    value=List(
                        elts=[
                            Starred(Subscript(
                                value=Name('a', Load()),
                                slice=Slice(upper=Constant(i)),
                                ctx=Load()
                            ), Load()),
                            self._parse_const(v, Load()),
                            Starred(Subscript(
                                value=Name('a', Load()),
                                slice=Slice(lower=Constant(i + 1)),
                                ctx=Load()
                            ), Load())
                        ],
                        ctx=Load()
                    )
                )
                loader_asm.append(ass_statement)
        random.shuffle(loader_asm)
        finished_asm = FunctionDef(
            name=func_name,
            args=arguments(posonlyargs=[],
                           args=[],
                           kwonlyargs=[],
                           kw_defaults=[],
                           defaults=[]),
            decorator_list=[],
            body=[
                Assign(  # a = []
                    targets=[Name('a', Store())],
                    value=BinOp(
                        left=List(
                            elts=[Constant(None)],
                            ctx=Load()
                        ),
                        op=Mult(),
                        right=Constant(len(collected_args))
                    )
                ),
                *loader_asm,
                Return(
                    Call(
                        func=Call(
                            func=Name('type', Load()),
                            args=[
                                Attribute(
                                    value=Name('b', Load()),
                                    attr='__code__',
                                    ctx=Load()
                                )
                            ],
                            keywords=[]
                        ),
                        args=[
                            Starred(
                                Name('a', Load()),
                                Load()
                            )
                        ],
                        keywords=[]
                    )
                )
            ],
            type_ignores=[]
        )
        return finished_asm

    def do_enc_pass(self, ast_mod: AST) -> Module:
        """
        forgive me
        """
        compiled_code_obj: CodeType = compile(ast_mod, "", "exec", optimize=2)
        dumped = marshal.dumps(compiled_code_obj)
        orig_fnc = FunctionDef(
            name="b",
            args=arguments(posonlyargs=[],
                           args=[],
                           kwonlyargs=[],
                           kw_defaults=[],
                           defaults=[]),
            decorator_list=[],
            body=[
                Expr(
                    Call(
                        func=Name('print', Load()),
                        args=[
                            Constant("what'cha looking for?")
                        ],
                        keywords=[]
                    )
                ),
                *[Assign(
                    targets=[Name(rnd_name(), Store()) for _ in range(random.randint(3, 5))],
                    value=Constant(random.randint(0, 65535))
                ) for _ in range(random.randint(3, 5))]
            ],
            type_ignores=[]
        )
        fix_missing_locations(orig_fnc)
        p = compile(Module(
            body=[orig_fnc],
            type_ignores=[]
        ), "", "exec", optimize=2)
        key = hashlib.md5(
            "".join(map(repr, [p.co_consts[0].co_code, *p.co_consts[0].co_consts, *p.co_consts[0].co_names,
                               *p.co_consts[0].co_varnames])).encode(
                "utf8")).digest()
        aes = AES.new(key, AES.MODE_EAX)
        encrypted = aes.encrypt_and_digest(dumped)
        nonce = aes.nonce
        loader = Module(
            body=[
                ast_import_from("Crypto.Cipher", "AES"),
                Expr(
                    Call(
                        func=Name('exec', Load()),
                        args=[
                            Call(
                                func=Attribute(
                                    value=ast_import_full("marshal"),
                                    attr="loads",
                                    ctx=Load()
                                ),
                                args=[
                                    Call(
                                        func=Attribute(
                                            value=Call(
                                                func=Attribute(
                                                    value=Name("AES", Load()),
                                                    attr="new",
                                                    ctx=Load()
                                                ),
                                                args=[
                                                    Call(
                                                        func=Attribute(
                                                            value=Call(
                                                                func=Attribute(
                                                                    value=ast_import_full("hashlib"),
                                                                    attr="md5",
                                                                    ctx=Load()
                                                                ),
                                                                args=[
                                                                    Call(
                                                                        func=Attribute(
                                                                            value=Call(
                                                                                func=Attribute(
                                                                                    value=Constant(''),
                                                                                    attr='join',
                                                                                    ctx=Load()),
                                                                                args=[
                                                                                    Call(
                                                                                        func=Name('map', Load()),
                                                                                        args=[
                                                                                            Name('repr', Load()),
                                                                                            List(
                                                                                                elts=[
                                                                                                    Attribute(
                                                                                                        Attribute(
                                                                                                            Name(
                                                                                                                'b',
                                                                                                                Load()),
                                                                                                            '__code__',
                                                                                                            Load()),
                                                                                                        'co_code',
                                                                                                        Load()),
                                                                                                    Starred(
                                                                                                        Attribute(
                                                                                                            Attribute(
                                                                                                                Name(
                                                                                                                    'b',
                                                                                                                    Load()),
                                                                                                                '__code__',
                                                                                                                Load()),
                                                                                                            'co_consts',
                                                                                                            Load()),
                                                                                                        Load()),
                                                                                                    Starred(
                                                                                                        Attribute(
                                                                                                            Attribute(
                                                                                                                Name(
                                                                                                                    'b',
                                                                                                                    Load()),
                                                                                                                '__code__',
                                                                                                                Load()),
                                                                                                            'co_names',
                                                                                                            Load()),
                                                                                                        Load()),
                                                                                                    Starred(
                                                                                                        Attribute(
                                                                                                            Attribute(
                                                                                                                Name(
                                                                                                                    'b',
                                                                                                                    Load()),
                                                                                                                '__code__',
                                                                                                                Load()),
                                                                                                            'co_varnames',
                                                                                                            Load()),
                                                                                                        Load())],
                                                                                                ctx=Load())],
                                                                                        keywords=[])],
                                                                                keywords=[]),
                                                                            attr='encode',
                                                                            ctx=Load()),
                                                                        args=[
                                                                            Constant('utf8')],
                                                                        keywords=[])
                                                                ],
                                                                keywords=[]
                                                            ),
                                                            attr="digest",
                                                            ctx=Load()
                                                        ),
                                                        args=[],
                                                        keywords=[]
                                                    ),
                                                    Constant(9),  # MODE_EAX
                                                    Constant(nonce)
                                                ],
                                                keywords=[]
                                            ),
                                            attr="decrypt",
                                            ctx=Load()
                                        ),
                                        args=[
                                            Constant(encrypted[0])
                                        ],
                                        keywords=[]
                                    )
                                ],
                                keywords=[]
                            )
                        ],
                        keywords=[]
                    )
                )
            ],
            type_ignores=[]
        )
        fix_missing_locations(loader)
        compiled_code_obj: CodeType = compile(loader, "", "exec", optimize=2)
        tn = rnd_name()
        main_loader = self.create_code_obj_loader(tn, compiled_code_obj)

        return Module(
            type_ignores=[],
            body=[
                orig_fnc,
                main_loader,
                Expr(Call(
                    func=Name('exec', Load()),
                    args=[
                        Call(
                            func=Name(tn, Load()),
                            args=[],
                            keywords=[]
                        )
                    ],
                    keywords=[]
                ))
            ]
        )

    def transform(self, ast: AST, current_file_name, all_asts, all_file_names) -> AST | Module:
        if sys.version_info[0] < 3 or sys.version_info[1] < 11:
            self.console.log("Python [bold]3.11[/bold] or up is required to use dynamicCodeObjLauncher, skipping", style="red")
            return ast
        ast_mod = fix_missing_locations(ast)
        if self.config["encrypt"].value:
            return self.do_enc_pass(ast_mod)
        else:
            compiled_code_obj: CodeType = compile(ast_mod, bytes([0xDA, 0xAF, 0x1A, 0x87, 0xFF]), "exec", optimize=2)
            all_code_objs = self.get_all_code_objects(self.args_from_co(compiled_code_obj))

            loaders = []
            for x in all_code_objs:  # create names first...
                name = rnd_name()
                self.code_obj_dict[x] = name
            for x in all_code_objs:  # ... then use them
                loaders.append(self.create_code_obj_loader(self.code_obj_dict[x], x))
            main = rnd_name()
            return Module(
                type_ignores=[],
                body=[
                    FunctionDef(
                        name='b',
                        args=arguments(
                            posonlyargs=[],
                            args=[],
                            kwonlyargs=[],
                            kw_defaults=[],
                            defaults=[]),
                        body=[Pass()],
                        decorator_list=[]),
                    *loaders,
                    self.create_code_obj_loader(main, compiled_code_obj),
                    Expr(Call(
                        func=Name('exec', Load()),
                        args=[
                            Call(
                                func=Name(main, Load()),
                                args=[],
                                keywords=[]
                            )
                        ],
                        keywords=[]
                    ))
                ]
            )
